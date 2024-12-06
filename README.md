# Melodify Bot 🎵

Bot de Telegram para descargar música desde YouTube con metadatos completos y opciones de personalización.

## Características ✨

- Descarga canciones individuales y playlists de YouTube
- Soporte para metadatos completos (título, artista, portada)
- Múltiples calidades de audio (128kbps y 320kbps)
- Soporte multiidioma (Español e Inglés)
- Descarga de playlists en archivo ZIP
- Sistema de donaciones integrado

## Requisitos previos 📋

- Python 3.8 o superior
- FFmpeg instalado en el sistema
- Token de bot de Telegram (obtenido de @BotFather)

## Instalación 🔧

1. Clona el repositorio:
```bash
git clone https://github.com/tuusuario/melodify-bot.git
cd melodify-bot
```

2. Instala las dependencias:
```bash
pip install -r requirements.txt
```

3. Configura el token del bot:
   - Abre `config.py`
   - Reemplaza 'YOUR_BOT_TOKEN' con tu token de Telegram

4. Ejecuta el bot:
```bash
python Melodify.py
```

## Uso 📱

1. Inicia el bot en Telegram con `/start`
2. Envía un enlace de YouTube (canción o playlist)
3. Selecciona la calidad de audio deseada
4. Espera a que se complete la descarga

## Comandos disponibles 🎮

- `/start` - Inicia el bot y muestra el menú principal
- `/cancel` - Cancela la descarga actual

## Configuración ⚙️

- Idioma: Español o Inglés
- Calidad de audio: 128kbps o 320kbps

## Contribuir 🤝

Las contribuciones son bienvenidas. Por favor, abre un issue primero para discutir los cambios que te gustaría hacer.

## Licencia 📄

Este proyecto está bajo la Licencia MIT - ver el archivo [LICENSE](LICENSE) para más detalles. 