# 📰 Lector RSS de terminal con traducción automática

Este proyecto es un lector RSS para terminal centrado en una necesidad concreta: 
**leer noticias en español independientemente del idioma original**.

## ✨ Características

- 📡 **Lectura de feeds RSS** desde la terminal  
- 🌍 **Traducción automática al español** de los artículos  
- 🧹 **Scraping ligero** para extraer y limpiar el contenido de las noticias  
- 🗄️ **Persistencia en SQLite3** para almacenar artículos y metadatos  
- 🎯 **Filtros específicos por sitio**, adaptados manualmente para mejorar la calidad del contenido  

## ⚙️ Cómo funciona

El flujo es sencillo:

1. Se obtienen los feeds RSS configurados  
2. Se hace scraping del contenido completo de cada artículo  
3. Se procesa y limpia el texto para facilitar la lectura  
4. Se traduce automáticamente al español  
5. Se guarda todo en una base de datos SQLite3  

## ⚠️ Limitaciones

- ❌ No es un lector RSS universal  
- 🔧 Los filtros son **estáticos y algo rústicos**, diseñados para sitios concretos  
- 🌐 La calidad del scraping depende de la estructura de cada web  

## 🤔 Motivación

Existen muchos lectores RSS en terminal, pero no encontré ninguno que integrase **traducción automática de forma sencilla**, así que decidí construir uno.

## 📜 Licencia

Distribuido bajo licencia **GPL v3**.
