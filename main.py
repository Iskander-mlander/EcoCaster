#!/usr/bin/env python3
"""
EcoCaster - Lector RSS con Traducción Automática
"""

import sys
import os
import argparse
import sqlite3
import time
import threading
import queue
import curses
import re

os.environ["PYTHONUNBUFFERED"] = "1"


class Database:
    def __init__(self, path="ecocaster.db"):
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self._init_tables()
    
    def _init_tables(self):
        cur = self.conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS feeds (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                url TEXT UNIQUE NOT NULL,
                title TEXT NOT NULL,
                description TEXT,
                link TEXT,
                language TEXT,
                etag TEXT,
                last_modified TEXT,
                active INTEGER DEFAULT 1,
                last_fetched TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS articles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                feed_id INTEGER NOT NULL,
                guid TEXT NOT NULL,
                title TEXT NOT NULL,
                content TEXT,
                summary TEXT,
                url TEXT,
                image_url TEXT,
                image_caption TEXT,
                author TEXT,
                language TEXT,
                published TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (feed_id) REFERENCES feeds(id) ON DELETE CASCADE,
                UNIQUE(feed_id, guid)
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS translations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                article_id INTEGER NOT NULL,
                source_lang TEXT NOT NULL,
                target_lang TEXT NOT NULL,
                translated_title TEXT,
                translated_content TEXT,
                translated_summary TEXT,
                translated_caption TEXT,
                translator TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (article_id) REFERENCES articles(id) ON DELETE CASCADE
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS blacklist (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                domain TEXT UNIQUE NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        self.conn.commit()
    
    def execute(self, sql, params=None):
        cur = self.conn.cursor()
        cur.execute(sql, params or ())
        self.conn.commit()
        return cur
    
    def fetchall(self, sql, params=None):
        cur = self.conn.cursor()
        cur.execute(sql, params or ())
        return cur.fetchall()
    
    def fetchone(self, sql, params=None):
        cur = self.conn.cursor()
        cur.execute(sql, params or ())
        return cur.fetchone()
    
    def add_blacklist_domain(self, domain):
        domain = domain.lower().strip()
        try:
            self.execute("INSERT OR IGNORE INTO blacklist (domain) VALUES (?)", (domain,))
            return True
        except:
            return False
    
    def remove_blacklist_domain(self, domain):
        domain = domain.lower().strip()
        self.execute("DELETE FROM blacklist WHERE domain = ?", (domain,))
    
    def get_blacklist_domains(self):
        return [r["domain"] for r in self.fetchall("SELECT domain FROM blacklist ORDER BY domain")]
    
    def is_blacklisted(self, domain):
        domain = domain.lower().strip()
        result = self.fetchone("SELECT id FROM blacklist WHERE domain = ?", (domain,))
        return result is not None
    
    def close(self):
        self.conn.close()


class Translator:
    def __init__(self):
        import requests
        self.session = requests.Session()
        self.endpoints = [
            "https://translate.googleapis.com/translate_a/single",
            "https://translate.google.com/translate_a/single",
        ]
        self.last_request = 0
    
    def detect_lang(self, text):
        if not text:
            return "en"
        cyrillic = sum(1 for c in text if '\u0400' <= c <= '\u04FF')
        if len(text) > 0 and cyrillic / len(text) > 0.3:
            return "ru"
        latin = sum(1 for c in text if 'a' <= c.lower() <= 'z')
        if len(text) > 0 and latin / len(text) > 0.3:
            return "en"
        return "en"
    
    def translate(self, text, source="auto", target="es"):
        if not text or len(text.strip()) < 3:
            return None
        
        text = text[:4000]
        
        import time
        for endpoint in self.endpoints:
            elapsed = time.time() - self.last_request
            if elapsed < 1.5:
                time.sleep(1.5 - elapsed)
            
            try:
                params = {
                    "client": "gtx",
                    "sl": source if source != "auto" else "auto",
                    "tl": target,
                    "dt": "t",
                    "q": text,
                }
                resp = self.session.get(endpoint, params=params, timeout=20)
                self.last_request = time.time()
                
                if resp.status_code == 200:
                    data = resp.json()
                    if data and data[0]:
                        result = "".join(item[0] for item in data[0] if item and item[0])
                        if result and len(result.strip()) > 0:
                            return result
            except:
                continue
        return None


class BackgroundTranslator:
    def __init__(self, db_path):
        self.db_path = db_path
        self.translator = Translator()
        self.queue = queue.Queue()
        self.running = True
        self.thread = threading.Thread(target=self._worker, daemon=True)
        self.thread.start()
    
    def translate_async(self, article_id):
        try:
            self.queue.put(article_id)
        except:
            pass
    
    def _worker(self):
        while self.running:
            try:
                article_id = self.queue.get(timeout=1)
                self._translate_article(article_id)
            except:
                pass
    
    def _translate_article(self, article_id):
        db = Database(self.db_path)
        
        existing = db.fetchone(
            "SELECT id FROM translations WHERE article_id = ?",
            (article_id,)
        )
        if existing:
            db.close()
            return
        
        article_row = db.fetchone("SELECT * FROM articles WHERE id = ?", (article_id,))
        if not article_row:
            db.close()
            return
        
        article = dict(article_row)
        
        source = article.get("language") or "en"
        if source in (None, "auto", ""):
            source = self.translator.detect_lang(article.get("title") or "")
        
        translated_title = self.translator.translate(article.get("title") or "", source, "es")
        
        translated_content = None
        if article.get("content") and len(article.get("content", "")) > 10:
            translated_content = self.translator.translate(article.get("content"), source, "es")
        
        translated_summary = None
        if article.get("summary") and len(article.get("summary", "")) > 10:
            translated_summary = self.translator.translate(article.get("summary"), source, "es")
        
        if not translated_title:
            db.close()
            return
        
        db.execute(
            """INSERT INTO translations 
               (article_id, source_lang, target_lang, translated_title, translated_content, 
                translated_summary, translator)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (article_id, source, "es", translated_title, translated_content, translated_summary, "Google")
        )
        db.close()
    
    def stop(self):
        self.running = False


class FeedFetcher:
    def __init__(self):
        import requests
        import feedparser
        
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "EcoCaster/1.0 RSS Reader",
            "Accept": "application/rss+xml, application/atom+xml, */*",
        })
        self.feedparser = feedparser
    
    def fetch(self, url):
        try:
            resp = self.session.get(url, timeout=30)
            resp.raise_for_status()
            parsed = self.feedparser.parse(resp.text)
            
            if not parsed.entries:
                return None
            
            feed = parsed.feed or {}
            entries = []
            
            for entry in parsed.entries:
                title = entry.get("title", "Untitled") or "Untitled"
                link = entry.get("link", "")
                
                full_content = ""
                if hasattr(entry, "content") and entry.content:
                    full_content = entry.content[0].value
                if not full_content and hasattr(entry, "summary_detail"):
                    full_content = entry.summary_detail.get("value", "")
                if not full_content and hasattr(entry, "description"):
                    full_content = entry.description
                
                summary = ""
                if hasattr(entry, "summary"):
                    summary = entry.summary
                elif hasattr(entry, "description"):
                    summary = entry.description
                
                published = None
                if hasattr(entry, "published_parsed") and entry.published_parsed:
                    try:
                        from datetime import datetime
                        published = datetime(*entry.published_parsed[:6]).isoformat()
                    except:
                        pass
                
                lang = entry.get("language") or feed.get("language", "en")
                
                entries.append({
                    "guid": entry.get("id") or link or title,
                    "title": title,
                    "content": full_content[:8000],
                    "summary": summary[:2000],
                    "url": link,
                    "author": entry.get("author", ""),
                    "published": published,
                    "language": lang,
                })
            
            return {
                "title": feed.get("title", "Untitled Feed") or "Untitled Feed",
                "description": feed.get("description", ""),
                "link": feed.get("link", ""),
                "language": feed.get("language", "en"),
                "entries": entries,
            }
        except Exception as e:
            return None


class App:
    def __init__(self, stdscr):
        self.stdscr = stdscr
        curses.curs_set(0)
        
        if curses.has_colors():
            curses.start_color()
            curses.use_default_colors()
            
            curses.init_pair(1, curses.COLOR_WHITE, curses.COLOR_BLACK)
            curses.init_pair(2, curses.COLOR_CYAN, curses.COLOR_BLACK)
            curses.init_pair(3, curses.COLOR_GREEN, curses.COLOR_BLACK)
            curses.init_pair(4, curses.COLOR_YELLOW, curses.COLOR_BLACK)
            curses.init_pair(7, curses.COLOR_MAGENTA, curses.COLOR_BLACK)
            curses.init_pair(5, curses.COLOR_BLACK, curses.COLOR_BLACK)
        
        self.db = Database()
        self.fetcher = FeedFetcher()
        self.bg_translator = BackgroundTranslator("ecocaster.db")
        
        self.feeds = []
        self.articles = []
        self.blacklist_domains = []
        self.selected = 0
        self.view = "list"
        self.selected_article_id = None
    
    def load_data(self):
        self.feeds = [dict(r) for r in self.db.fetchall("SELECT * FROM feeds ORDER BY title")]
        
        blacklist = self.db.get_blacklist_domains()
        
        all_articles = self.db.fetchall("""
            SELECT a.id, a.title, a.published, a.language, a.url, f.title as feed_title,
                   t.translated_title
            FROM articles a
            JOIN feeds f ON a.feed_id = f.id
            LEFT JOIN translations t ON t.article_id = a.id
            ORDER BY a.published DESC
            LIMIT 200
        """)
        
        self.articles = []
        from urllib.parse import urlparse
        for r in all_articles:
            article = dict(r)
            url = article.get("url", "")
            if url:
                domain = urlparse(url).netloc.lower()
                
                is_blocked = False
                for blocked in blacklist:
                    if domain == blocked or domain.endswith("." + blocked):
                        is_blocked = True
                        break
                
                if not is_blocked:
                    self.articles.append(article)
            else:
                self.articles.append(article)
        
        untranslated_ids = [a["id"] for a in self.articles if not a.get("translated_title")][:10]
        for aid in untranslated_ids:
            self.bg_translator.translate_async(aid)
    
    def add_feed(self, url):
        info = self.fetcher.fetch(url)
        if not info:
            return False
        
        cur = self.db.execute(
            "INSERT INTO feeds (url, title, description, link, language) VALUES (?, ?, ?, ?, ?)",
            (url, info["title"], info["description"], info["link"], info["language"])
        )
        feed_id = cur.lastrowid
        
        for entry in info["entries"]:
            try:
                self.db.execute(
                    """INSERT OR IGNORE INTO articles 
                       (feed_id, guid, title, content, summary, url, author, language, published)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (feed_id, entry["guid"], entry["title"], entry["content"], entry["summary"],
                     entry["url"], entry["author"], entry["language"], entry["published"])
                )
            except:
                pass
        
        return True
    
    def refresh_feeds(self):
        total = 0
        for feed in self.feeds:
            info = self.fetcher.fetch(feed["url"])
            if not info:
                continue
            
            for entry in info["entries"]:
                try:
                    self.db.execute(
                        """INSERT OR IGNORE INTO articles 
                           (feed_id, guid, title, content, summary, url, author, language, published)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (feed["id"], entry["guid"], entry["title"], entry["content"], entry["summary"],
                         entry["url"], entry["author"], entry["language"], entry["published"])
                    )
                    total += 1
                except:
                    pass
        
        return total
    
    def _clean_text(self, text):
        if not text:
            return ""
        text = text.replace("<br>", "\n").replace("<br/>", "\n").replace("<br />", "\n")
        text = text.replace("<p>", "\n").replace("</p>", "\n")
        text = text.replace("&nbsp;", " ").replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
        text = text.replace("</a>", "").replace("</b>", "").replace("</i>", "").replace("</em>", "")
        text = re.sub(r'<script[^>]*>.*?</script>', '', text, flags=re.DOTALL)
        text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL)
        text = re.sub(r'<[^>]+>', '', text)
        text = text.replace("\n\n\n", "\n").replace("\n\n", "\n")
        return text.strip()
    
    def _wrap_text(self, text, width):
        words = text.split()
        lines = []
        current_line = []
        current_length = 0
        
        for word in words:
            word_len = len(word)
            
            if current_length + word_len + len(current_line) <= width:
                current_line.append(word)
                current_length += word_len
            else:
                if current_line:
                    lines.append(" ".join(current_line))
                current_line = [word]
                current_length = word_len
        
        if current_line:
            lines.append(" ".join(current_line))
        
        return lines
    
    def render(self):
        self.stdscr.erase()
        h, w = self.stdscr.getmaxyx()
        
        self.stdscr.addstr(0, 0, " EcoCaster - Lector RSS ", curses.A_BOLD | curses.color_pair(7))
        
        if self.view == "list":
            self._render_list(h, w)
        elif self.view == "feeds":
            self._render_feeds(h, w)
        elif self.view == "view":
            self._render_view(h, w)
        elif self.view == "blacklist":
            self._render_blacklist(h, w)
        
        self._render_footer(h, w)
        self.stdscr.refresh()
    
    def _render_list(self, h, w):
        if self.feeds:
            self.stdscr.addstr(1, 0, " Feeds: ", curses.A_BOLD)
            feed_str = " | ".join(f["title"][:30] for f in self.feeds[:3])
            if len(self.feeds) > 3:
                feed_str += f" (+{len(self.feeds)-3})"
            self.stdscr.addstr(1, 8, feed_str[:w-10], curses.color_pair(5))
        
        start_y = 3
        per_page = h - 6
        
        if self.articles:
            start_idx = self.selected
            end_idx = min(start_idx + per_page, len(self.articles))
            
            for i in range(start_idx, end_idx):
                a = self.articles[i]
                y = start_y + (i - start_idx)
                
                if y >= h - 2:
                    break
                
                title = a.get("translated_title") or a.get("title", "Sin título")
                title = title[:w - 15]
                date = a.get("published", "")[:10]
                has_trans = "✓" if a.get("translated_title") else " "
                
                if i == self.selected:
                    self.stdscr.addstr(y, 0, f"→ [{has_trans}] {title} {date}", curses.color_pair(2) | curses.A_BOLD)
                else:
                    self.stdscr.addstr(y, 0, f"  [{has_trans}] {title} {date}", curses.A_NORMAL)
        else:
            self.stdscr.addstr(start_y, 0, " No hay articulos ", curses.color_pair(5))
    
    def _render_feeds(self, h, w):
        self.stdscr.addstr(1, 0, " Lista de Feeds ", curses.A_BOLD | curses.color_pair(7))
        
        for i, f in enumerate(self.feeds):
            y = 3 + i * 2
            if y >= h - 2:
                break
            self.stdscr.addstr(y, 0, f"[{i+1}] {f['title'][:w-8]}", curses.A_NORMAL)
            self.stdscr.addstr(y + 1, 0, f"     {f['url'][:w-8]}", curses.color_pair(5))
    
    def _render_view(self, h, w):
        if not self.selected_article_id:
            return
        
        article = self.db.fetchone("""
            SELECT a.*, f.title as feed_title,
                   t.translated_title, t.translated_content, t.translated_summary
            FROM articles a
            JOIN feeds f ON a.feed_id = f.id
            LEFT JOIN translations t ON t.article_id = a.id
            WHERE a.id = ?
        """, (self.selected_article_id,))
        
        if not article:
            return
        
        article = dict(article)
        
        title = article.get("translated_title") or article.get("title", "Sin título")
        if len(title) > w - 2:
            title = title[:w - 5] + "..."
        self.stdscr.addstr(1, 0, title, curses.A_BOLD | curses.color_pair(1))
        
        y = 3
        
        text = ""
        if article.get("translated_content"):
            text = article["translated_content"]
        elif article.get("translated_summary"):
            text = article["translated_summary"]
        elif article.get("content"):
            text = article["content"]
        elif article.get("summary"):
            text = article["summary"]
        
        text = self._clean_text(text)
        
        if text:
            raw_lines = text.split("\n")
            wrapped_lines = []
            for raw_line in raw_lines:
                raw_line = raw_line.strip()
                if not raw_line:
                    wrapped_lines.append("")
                    continue
                wrapped_lines.extend(self._wrap_text(raw_line, w - 2))
            
            for line in wrapped_lines:
                if y >= h - 4:
                    break
                if not line:
                    y += 1
                    continue
                self.stdscr.addstr(y, 0, line, curses.A_NORMAL)
                y += 1
        else:
            self.stdscr.addstr(y, 0, "No hay contenido disponible", curses.color_pair(5))
        
        y = h - 3
        url = article.get("url", "")
        self.stdscr.addstr(y, 0, f"Feed: {article.get('feed_title', '')} | Idioma: {article.get('language', '')}", curses.color_pair(5))
        
        if url:
            y = h - 2
            self.stdscr.addstr(y, 0, f"[O] Abrir en navegador: {url[:w-2]}", curses.color_pair(3) | curses.A_BOLD)
    
    def _render_blacklist(self, h, w):
        self.stdscr.addstr(1, 0, " Dominios Bloqueados ", curses.A_BOLD | curses.color_pair(7))
        
        self.blacklist_domains = self.db.get_blacklist_domains()
        
        for i, domain in enumerate(self.blacklist_domains):
            y = 3 + i
            if y >= h - 2:
                break
            if i == self.selected:
                self.stdscr.addstr(y, 0, f"→ {domain}", curses.color_pair(2) | curses.A_BOLD)
            else:
                self.stdscr.addstr(y, 0, f"  {domain}", curses.A_NORMAL)
        
        if not self.blacklist_domains:
            self.stdscr.addstr(3, 0, " No hay dominios bloqueados ", curses.color_pair(5))
    
    def _render_footer(self, h, w):
        if self.view == "list":
            keys = "[F2]Añadir  [F3]Feeds  [F6]Bloquear  [F4]Actualizar  [F5]Salir"
        elif self.view == "feeds":
            keys = "[F2]Añadir  [F6]Bloquear  [ESC]Volver  | [1-9]Seleccionar"
        elif self.view == "view":
            keys = "[ESC]Volver  [F4]Traducir  [O]AbrirURL"
        elif self.view == "blacklist":
            keys = "[A]ñadir  [E]liminar  [ESC]Volver"
        
        self.stdscr.addstr(h - 1, 0, keys[:w-1], curses.A_BOLD)
    
    def run(self):
        self.load_data()
        
        while True:
            self.render()
            key = self.stdscr.getch()
            
            if self.view == "list":
                if key == curses.KEY_UP:
                    self.selected = max(0, self.selected - 1)
                elif key == curses.KEY_DOWN:
                    self.selected = min(len(self.articles) - 1, self.selected + 1)
                elif key == curses.KEY_RIGHT or key == ord('\n') or key == 13:
                    self._view_article()
                elif key == curses.KEY_LEFT:
                    self.view = "feeds"
                elif key in (ord('k'), ord('K')):
                    self.selected = max(0, self.selected - 1)
                elif key in (ord('j'), ord('J')):
                    self.selected = min(len(self.articles) - 1, self.selected + 1)
                elif key == curses.KEY_F2 or key == ord('2'):
                    self._add_feed_dialog()
                elif key == curses.KEY_F3 or key == ord('3'):
                    self.view = "feeds"
                elif key == curses.KEY_F6 or key == 270:
                    self.blacklist_domains = self.db.get_blacklist_domains()
                    self.view = "blacklist"
                    self.selected = 0
                elif key == curses.KEY_F4 or key == ord('4'):
                    self._refresh_action()
                elif key == curses.KEY_F5 or key == ord('5'):
                    break
            
            elif self.view == "feeds":
                if key >= ord('1') and key <= ord('9'):
                    idx = key - ord('1')
                    if idx < len(self.feeds):
                        self._select_feed(idx)
                elif key == curses.KEY_F2 or key == ord('2'):
                    self._add_feed_dialog()
                elif key == curses.KEY_F6 or key == 270:
                    self.blacklist_domains = self.db.get_blacklist_domains()
                    self.view = "blacklist"
                    self.selected = 0
                elif key == curses.KEY_LEFT or key == 27:
                    self.view = "list"
            
            elif self.view == "view":
                if key == curses.KEY_LEFT or key == 27:
                    self.view = "list"
                    self.load_data()
                elif key == curses.KEY_F4 or key == ord('4'):
                    self._translate_current()
                elif key in (ord('o'), ord('O')):
                    self._open_url()
            
            elif self.view == "blacklist":
                if key == ord('a') or key == ord('A'):
                    self._add_blacklist_dialog()
                elif key == ord('e') or key == ord('E'):
                    self._remove_blacklist_domain()
                elif key == curses.KEY_UP:
                    self.selected = max(0, self.selected - 1)
                elif key == curses.KEY_DOWN:
                    self.selected = min(len(self.blacklist_domains) - 1, self.selected + 1)
                elif key == curses.KEY_LEFT or key == 27:
                    self.view = "list"
                    self.load_data()
    
    def _view_article(self):
        if 0 <= self.selected < len(self.articles):
            self.selected_article_id = self.articles[self.selected]["id"]
            
            trans = self.db.fetchone(
                "SELECT id FROM translations WHERE article_id = ?",
                (self.selected_article_id,)
            )
            
            if not trans:
                self.bg_translator.translate_async(self.selected_article_id)
            
            self.view = "view"
    
    def _select_feed(self, idx):
        if 0 <= idx < len(self.feeds):
            blacklist = self.db.get_blacklist_domains()
            all_articles = self.db.fetchall(
                """SELECT a.id, a.title, a.published, a.language, a.url, f.title as feed_title,
                          t.translated_title
                   FROM articles a
                   JOIN feeds f ON a.feed_id = f.id
                   LEFT JOIN translations t ON t.article_id = a.id
                   WHERE a.feed_id = ?
                   ORDER BY a.published DESC
                   LIMIT 200""",
                (self.feeds[idx]["id"],)
            )
            filtered = []
            for r in all_articles:
                article = dict(r)
                if article.get("url"):
                    from urllib.parse import urlparse
                    domain = urlparse(article["url"]).netloc.lower()
                    blocked = False
                    for bl_domain in blacklist:
                        if domain == bl_domain or domain.endswith("." + bl_domain):
                            blocked = True
                            break
                    if not blocked:
                        filtered.append(article)
            self.articles = filtered
            self.selected = 0
            self.view = "list"
    
    def _add_feed_dialog(self):
        self.stdscr.addstr(curses.LINES - 3, 0, "URL del feed: ", curses.A_BOLD)
        curses.curs_set(1)
        self.stdscr.refresh()
        
        curses.echo()
        url = self.stdscr.getstr(curses.LINES - 3, 14).decode('utf-8')
        curses.noecho()
        curses.curs_set(0)
        
        if url.strip():
            self.add_feed(url.strip())
            self.load_data()
    
    def _add_blacklist_dialog(self):
        self.stdscr.addstr(curses.LINES - 3, 0, "Dominio a bloquear: ", curses.A_BOLD)
        curses.curs_set(1)
        self.stdscr.refresh()
        
        curses.echo()
        domain = self.stdscr.getstr(curses.LINES - 3, 22).decode('utf-8')
        curses.noecho()
        curses.curs_set(0)
        
        if domain.strip():
            self.db.add_blacklist_domain(domain.strip())
            self.blacklist_domains = self.db.get_blacklist_domains()
            self.load_data()
    
    def _remove_blacklist_domain(self):
        if 0 <= self.selected < len(self.blacklist_domains):
            domain = self.blacklist_domains[self.selected]
            self.db.remove_blacklist_domain(domain)
            self.blacklist_domains = self.db.get_blacklist_domains()
            self.selected = max(0, min(self.selected, len(self.blacklist_domains) - 1))
            self.load_data()
    
    def _refresh_action(self):
        self.refresh_feeds()
        self.load_data()
    
    def _translate_current(self):
        if self.selected_article_id:
            trans = self.db.fetchone(
                "SELECT id FROM translations WHERE article_id = ?",
                (self.selected_article_id,)
            )
            if not trans:
                self.bg_translator.translate_async(self.selected_article_id)
                time.sleep(3)
                self.load_data()
    
    def _open_url(self):
        if not self.selected_article_id:
            return
        article = self.db.fetchone("SELECT url FROM articles WHERE id = ?", (self.selected_article_id,))
        if article and article["url"]:
            import subprocess
            try:
                subprocess.Popen(["xdg-open", article["url"]])
            except:
                try:
                    subprocess.Popen(["gnome-open", article["url"]])
                except:
                    print(f"Abre esta URL en tu navegador: {article['url']}")
    
    def close(self):
        self.bg_translator.stop()
        self.db.close()


def main():
    parser = argparse.ArgumentParser(description="EcoCaster - Lector RSS")
    parser.add_argument("-a", "--add", metavar="URL", help="Añadir feed")
    parser.add_argument("-r", "--refresh", action="store_true", help="Actualizar")
    args = parser.parse_args()
    
    if args.add:
        db = Database()
        fetcher = FeedFetcher()
        
        print(f"Añadiendo: {args.add}")
        info = fetcher.fetch(args.add)
        if info:
            cur = db.execute(
                "INSERT INTO feeds (url, title, description, link, language) VALUES (?, ?, ?, ?, ?)",
                (args.add, info["title"], info["description"], info["link"], info["language"])
            )
            feed_id = cur.lastrowid
            for entry in info["entries"]:
                try:
                    db.execute(
                        """INSERT OR IGNORE INTO articles 
                           (feed_id, guid, title, content, summary, url, author, language, published)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (feed_id, entry["guid"], entry["title"], entry["content"], entry["summary"],
                         entry["url"], entry["author"], entry["language"], entry["published"])
                    )
                except:
                    pass
            print("✓ Feed añadido")
        else:
            print("✗ Error al añadir feed")
        db.close()
        return
    
    if args.refresh:
        db = Database()
        feeds = [dict(r) for r in db.fetchall("SELECT * FROM feeds")]
        fetcher = FeedFetcher()
        
        total = 0
        for feed in feeds:
            info = fetcher.fetch(feed["url"])
            if info:
                for entry in info["entries"]:
                    try:
                        db.execute(
                            """INSERT OR IGNORE INTO articles 
                               (feed_id, guid, title, content, summary, url, author, language, published)
                               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                            (feed["id"], entry["guid"], entry["title"], entry["content"], entry["summary"],
                             entry["url"], entry["author"], entry["language"], entry["published"])
                        )
                        total += 1
                    except:
                        pass
        
        print(f"Se añadieron {total} artículos")
        db.close()
        return
    
    curses.wrapper(lambda s: App(s).run())


if __name__ == "__main__":
    main()