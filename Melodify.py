from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters
from yt_dlp import YoutubeDL
import os
import zipfile
import asyncio
from typing import Dict, List, Optional, Tuple
import json
import re
import aiofiles
from cachetools import TTLCache
from db_connection import get_db_connection
import concurrent.futures
from functools import partial
from tenacity import retry, stop_after_attempt, wait_exponential
from telegram.error import TimedOut
import musicbrainzngs
from mutagen.mp3 import MP3
from mutagen.id3 import ID3, APIC, TIT2, TPE1, TALB, TDRC, TCON
import requests
from io import BytesIO

class MelodifyBot:
    def __init__(self):
        self.active_downloads: Dict[int, bool] = {}  # user_id: is_downloading
        self.user_settings: Dict[int, Dict] = {}  # user_id: {language: str, quality: str, metadata: bool}
        self.VAULT_CHAT_ID = VAULT_CHATID  # ID real del grupo boveda
        
        self.vault_cache = TTLCache(maxsize=100, ttl=300)
        self.translations = {
            "es": {
                "welcome": "¡Bienvenido a Melodify! 🎵\nEnvíame un enlace de YouTube para descargar música.\nPuedes enviar enlaces de canciones individuales o playlists.",
                "download_music": "🎵 Descargar Música",
                "settings": "⚙️ Configuración",
                "donate": "💝 Donar",
                "downloading": "⏳ Descargando...",
                "download_complete": "✅ Descarga completada",
                "download_error": "❌ Error en la descarga: {}",
                "already_downloading": "Ya hay una descarga en proceso. Usa /cancel para cancelarla.",
                "playlist_warning": "⚠️ La playlist contiene {} canciones.\n¿Deseas continuar con la descarga?",
                "confirm": "✅ Confirmar",
                "cancel": "❌ Cancelar",
                "language_settings": " Configuración de idioma",
                "quality_settings": "🎧 Configuración de calidad",
                "current_language": "Idioma actual: {}",
                "current_quality": "Calidad actual: {} kbps",
                "searching_vault": "🔍 Buscando en la boveda...",
                "found_in_vault": "✨ ¡Canción encontrada en la boveda!",
                "saving_to_vault": "💾 Guardando en la boveda...",
                "download_instructions": (
                    "📝 *Instrucciones para descargar:*\n\n"
                    "1️⃣ Copia el enlace de YouTube de la canción o playlist\n"
                    "2️⃣ Pégalo directamente en este chat\n\n"
                    "✅ *Formatos soportados:*\n"
                    "• Enlaces de canciones individuales\n"
                    "• Enlaces de playlists\n\n"
                    "⚡️ *Ejemplo:*\n"
                    "https://youtube.com/watch?v=ejemplo"
                ),
                "metadata_settings": "🎵 Metadatos",
                "metadata_enabled": "✅ Metadatos: Activados",
                "metadata_disabled": "❌ Metadatos: Desactivados",
                "metadata_description": "Los metadatos incluyen título, artista, álbum y carátula de la canción.",
            },
            "en": {
                "welcome": "Welcome to Melodify! 🎵\nSend me a YouTube link to download music.\nYou can send individual songs or playlists.",
                "download_music": "🎵 Download Music",
                "settings": "⚙️ Settings",
                "donate": "💝 Donate",
                "downloading": "⏳ Downloading...",
                "download_complete": "✅ Download complete",
                "download_error": "❌ Download error: {}",
                "already_downloading": "There's already an active download. Use /cancel to cancel it.",
                "playlist_warning": "⚠️ The playlist contains {} songs.\nDo you want to continue?",
                "confirm": "✅ Confirm",
                "cancel": "❌ Cancel",
                "language_settings": "🌍 Language Settings",
                "quality_settings": "🎧 Quality Settings",
                "current_language": "Current language: {}",
                "current_quality": "Current quality: {} kbps",
                "searching_vault": "🔍 Searching in vault...",
                "found_in_vault": "✨ Song found in vault!",
                "saving_to_vault": "💾 Saving to vault...",
                "download_instructions": (
                    "📝 *Download Instructions:*\n\n"
                    "1️⃣ Copy the YouTube link of the song or playlist\n"
                    "2️⃣ Paste it directly in this chat\n\n"
                    "✅ *Supported formats:*\n"
                    "• Single song links\n"
                    "• Playlist links\n\n"
                    "⚡️ *Example:*\n"
                    "https://youtube.com/watch?v=example"
                ),
                "metadata_settings": "🎵 Metadata",
                "metadata_enabled": "✅ Metadata: Enabled",
                "metadata_disabled": "❌ Metadata: Disabled",
                "metadata_description": "Metadata includes song title, artist, album and cover art.",
            }
        }
        
        self.thread_pool = concurrent.futures.ThreadPoolExecutor(max_workers=3)
        self.download_cache = TTLCache(maxsize=100, ttl=3600)  # Cache de 1 hora
        self.MAX_RETRIES = 3
        self.CHUNK_SIZE = 10 * 1024 * 1024  # 10MB chunks para envío
        
        # Configurar musicbrainzngs
        musicbrainzngs.set_useragent(
            "Melodify",
            "1.0",
            "https://t.me/your_bot"  # Reemplaza con el enlace real de tu bot
        )

    def get_text(self, user_id: int, key: str) -> str:
        # Si el usuario no existe en user_settings, inicializarlo con valores por defecto
        if user_id not in self.user_settings:
            self.user_settings[user_id] = {
                "language": "es",
                "quality": "320",
                "metadata": False  # Metadatos desactivados por defecto
            }
        lang = self.user_settings.get(user_id, {}).get("language", "es")
        return self.translations[lang].get(key, self.translations["es"][key])

    def sanitize_filename(self, filename: str) -> str:
        # Eliminar caracteres no permitidos y reemplazar espacios
        filename = re.sub(r'[<>:"/\\|?*]', '', filename)
        # Reemplazar espacios múltiples con un solo espacio
        filename = re.sub(r'\s+', ' ', filename)
        # Limitar la longitud del nombre del archivo
        if len(filename) > 200:
            filename = filename[:200]
        return filename.strip()

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        if user_id not in self.user_settings:
            self.user_settings[user_id] = {
                "language": "es",
                "quality": "320",
                "metadata": False  # Metadatos desactivados por defecto
            }
        
        keyboard = [
            [InlineKeyboardButton(self.get_text(user_id, "download_music"), callback_data="download_menu")],
            [InlineKeyboardButton(self.get_text(user_id, "settings"), callback_data="settings")],
            [InlineKeyboardButton(self.get_text(user_id, "donate"), callback_data="donate")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        # Verificar si es un callback_query o un mensaje nuevo
        if update.callback_query:
            await update.callback_query.edit_message_text(
                text=self.get_text(user_id, "welcome"),
                reply_markup=reply_markup
            )
        else:
            await update.message.reply_text(
                text=self.get_text(user_id, "welcome"),
                reply_markup=reply_markup
            )

    async def settings_menu(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        keyboard = [
            [InlineKeyboardButton("🇪🇸 Español", callback_data="lang_es"),
             InlineKeyboardButton("🇬🇧 English", callback_data="lang_en")],
            [InlineKeyboardButton("128 kbps", callback_data="quality_128"),
             InlineKeyboardButton("320 kbps", callback_data="quality_320")],
            [InlineKeyboardButton(
                "✅ Metadatos" if self.user_settings[user_id].get('metadata', False) else "❌ Metadatos", 
                callback_data="toggle_metadata"
            )],
            [InlineKeyboardButton("🔙 Volver", callback_data="back_to_main")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        current_settings = (
            f"{self.get_text(user_id, 'current_language').format(self.user_settings[user_id]['language'])}\n"
            f"{self.get_text(user_id, 'current_quality').format(self.user_settings[user_id]['quality'])}\n"
            f"{self.get_text(user_id, 'metadata_enabled' if self.user_settings[user_id].get('metadata', False) else 'metadata_disabled')}\n\n"
            f"{self.get_text(user_id, 'metadata_description')}"
        )
        
        if update.callback_query:
            await update.callback_query.edit_message_text(current_settings, reply_markup=reply_markup)
        else:
            await update.message.reply_text(current_settings, reply_markup=reply_markup)

    async def donate_menu(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        keyboard = [
            [InlineKeyboardButton("PayPal", url="https://www.paypal.com/donate/?hosted_button_id=YHWLT248VPCKA")],
            [InlineKeyboardButton("🔙 Volver", callback_data="back_to_main")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.callback_query.edit_message_text(
            "¡Gracias por considerar una donación! 💖\n"
            "Tu apoyo ayuda a mantener y mejorar Melodify.",
            reply_markup=reply_markup
        )

    async def cancel_download(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        if self.active_downloads.get(user_id):
            self.active_downloads[user_id] = False
            await update.message.reply_text("❌ Descarga cancelada")
        else:
            await update.message.reply_text("No hay descargas activas para cancelar")
            
            
    async def donar_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Manejador para el comando /donar"""
        keyboard = [
            [InlineKeyboardButton("PayPal", url="https://www.paypal.com/donate/?hosted_button_id=YHWLT248VPCKA")],
            [InlineKeyboardButton("🔙 Volver", callback_data="back_to_main")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            "¡Gracias por considerar una donación! 💖\n"
            "Tu apoyo ayuda a mantener y mejorar Melodify.",
            reply_markup=reply_markup
        )
    
    
    async def configuracion_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Manejador para el comando /configuracion"""
        user_id = update.effective_user.id
        keyboard = [
            [InlineKeyboardButton("🇪🇸 Español", callback_data="lang_es"),
             InlineKeyboardButton("🇬🇧 English", callback_data="lang_en")],
            [InlineKeyboardButton("128 kbps", callback_data="quality_128"),
             InlineKeyboardButton("320 kbps", callback_data="quality_320")],
            [InlineKeyboardButton("🔙 Volver", callback_data="back_to_main")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        current_settings = (
            f"{self.get_text(user_id, 'current_language').format(self.user_settings[user_id]['language'])}\n"
            f"{self.get_text(user_id, 'current_quality').format(self.user_settings[user_id]['quality'])}"
        )
        
        await update.message.reply_text(current_settings, reply_markup=reply_markup)

    

    async def callback_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        user_id = query.from_user.id
        data = query.data
        
        print(f"[DEBUG] Callback recibido: {data}")

        try:
            if data.startswith("lang_"):
                lang = data.split("_")[1]
                self.user_settings[user_id]["language"] = lang
                await self.settings_menu(update, context)
            
            elif data.startswith("quality_"):
                quality = data.split("_")[1]
                self.user_settings[user_id]["quality"] = quality
                await self.settings_menu(update, context)
            
            elif data == "settings":
                await self.settings_menu(update, context)
            
            elif data == "donate":
                await self.donate_menu(update, context)
            
            elif data == "back_to_main":
                await self.start(update, context)
            
            elif data == "playlist_confirm":
                print("[DEBUG] Iniciando descarga de playlist")
                self.active_downloads[user_id] = True

                try:
                    # Obtener información de la playlist
                    playlist_info = context.user_data.get('playlist_info')
                    if not playlist_info:
                        print("[DEBUG] Error: No se encontró información de playlist")
                        await query.edit_message_text("❌ Error: Información de playlist no encontrada")
                        return

                    # Guardar el mensaje original de confirmación antes de modificarlo
                    confirmation_message = query.message
                    
                    # Actualizar el mensaje de confirmación para mostrar solo el botón de cancelar
                    keyboard = [[InlineKeyboardButton("❌ Cancelar", callback_data="playlist_cancel")]]
                    await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(keyboard))
                    
                    # Crear mensaje de progreso
                    progress_message = await context.bot.send_message(
                        chat_id=update.effective_chat.id,
                        text=f"⏳ Iniciando descarga...\nProgreso: 0/{playlist_info['total']}"
                    )
                    
                    # Guardar referencias en context.user_data
                    context.user_data['progress_message'] = progress_message
                    context.user_data['confirmation_message'] = confirmation_message

                    # Iniciar descarga
                    await self._handle_playlist(update, context, playlist_info, playlist_info['ydl_opts'])
                    
                    # Mensaje final de éxito y programar su eliminación
                    final_message = await progress_message.edit_text("✅ Descarga de playlist completada")
                    
                    # Programar eliminación de mensajes después de 10 segundos
                    async def delete_messages():
                        await asyncio.sleep(10)
                        try:
                            # Eliminar mensaje final
                            await final_message.delete()
                            # Eliminar mensaje de confirmación
                            await confirmation_message.delete()
                        except Exception as e:
                            print(f"[DEBUG] Error al eliminar mensajes: {e}")
                    
                    asyncio.create_task(delete_messages())
                    
                except Exception as e:
                    print(f"[DEBUG] Error en descarga de playlist: {e}")
                    if 'progress_message' in context.user_data:
                        await context.user_data['progress_message'].edit_text(f"❌ Error: {str(e)}")
                
                finally:
                    self.active_downloads[user_id] = False
                    # Limpiar datos de contexto
                    for key in ['progress_message', 'playlist_info', 'confirmation_message']:
                        if key in context.user_data:
                            del context.user_data[key]

            elif data == "playlist_cancel":
                self.active_downloads[user_id] = False
                await query.edit_message_text("❌ Descarga de playlist cancelada")
                if 'playlist_info' in context.user_data:
                    del context.user_data['playlist_info']
            
            elif data == "download_menu":
                keyboard = [
                    [InlineKeyboardButton(self.get_text(user_id, "download_music"), callback_data="download_menu")],
                    [InlineKeyboardButton(self.get_text(user_id, "settings"), callback_data="settings")],
                    [InlineKeyboardButton(self.get_text(user_id, "donate"), callback_data="donate")]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
                
                await query.edit_message_text(
                    text=self.get_text(user_id, "download_instructions"),
                    parse_mode='Markdown',
                    disable_web_page_preview=True,
                    reply_markup=reply_markup
                )

            elif data == "toggle_metadata":
                # Toggle el estado de los metadatos
                current_state = self.user_settings[user_id].get('metadata', False)
                self.user_settings[user_id]['metadata'] = not current_state
                await self.settings_menu(update, context)

        except Exception as e:
            print(f"[DEBUG] Error en callback_handler: {e}")
            # Intentar enviar un mensaje de error al usuario
            try:
                await query.answer("Ha ocurrido un error. Por favor, intenta de nuevo.")
            except:
                pass

    async def handle_url(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        url = update.message.text
        user_id = update.effective_user.id
        
            # Verificar si el usuario está inicializado
        if user_id not in self.user_settings:
            await update.message.reply_text(
                "Por favor, inicia el bot primero usando el comando /start"
            )
            return
        
        print(f"[DEBUG] Iniciando handle_url con URL: {url}")
        
        # Validar que sea una URL de YouTube
        youtube_patterns = [
            r'(?:https?://)?(?:www\.)?(?:youtube\.com|youtu\.be)/(?:watch\?v=)?[\w-]+',
            r'(?:https?://)?(?:www\.)?youtube\.com/playlist\?list=[\w-]+'
        ]
        
        is_youtube_url = any(re.match(pattern, url) for pattern in youtube_patterns)
        
        if not is_youtube_url:
            await update.message.reply_text(
                "❌ *Error: URL no válida*\n\n"
                "Por favor, envía solo enlaces de YouTube.\n\n"
                "✅ *Enlaces válidos:*\n"
                "• https://youtube.com/watch?v=...\n"
                "• https://youtu.be/...\n"
                "• https://youtube.com/playlist?list=...",
                parse_mode='Markdown'
            )
            return
        
        if self.active_downloads.get(user_id):
            await update.message.reply_text(
                self.get_text(user_id, "already_downloading")
            )
            return
        
        try:
            # Primero, verificamos si es una playlist
            playlist_opts = {
                'extract_flat': True,
                'quiet': True,
                'no_warnings': True,
                'extract_flat': 'in_playlist'
            }
            
            print("[DEBUG] Verificando si es playlist...")
            try:
                with YoutubeDL(playlist_opts) as ydl:
                    result = ydl.extract_info(url, download=False)
                    
                    if not result:
                        raise Exception("No se pudo obtener información del video/playlist")
                    
                    is_playlist = bool(result.get('entries'))
                    print(f"[DEBUG] ¿Es playlist?: {is_playlist}")
                    
                    if is_playlist:
                        if not result['entries']:
                            await update.message.reply_text(
                                "❌ La playlist está vacía o es privada."
                            )
                            return
                        
                        print(f"[DEBUG] Detectada playlist con {len(result['entries'])} canciones")
                        ydl_opts = {
                            'format': 'bestaudio/best',
                            'postprocessors': [{
                                'key': 'FFmpegExtractAudio',
                                'preferredcodec': self.user_settings[user_id]["quality"],
                            }],
                            'extract_flat': True,
                            'add_metadata': True,
                            'writethumbnail': True,
                            'noplaylist': False,
                        }
                        # Enviar mensaje de confirmación al usuario antes de descargar
                        total_songs = len(result['entries'])
                        keyboard = [
                            [InlineKeyboardButton(self.get_text(user_id, "confirm"), callback_data="playlist_confirm")],
                            [InlineKeyboardButton(self.get_text(user_id, "cancel"), callback_data="playlist_cancel")]
                        ]
                        reply_markup = InlineKeyboardMarkup(keyboard)
                        context.user_data['playlist_info'] = {
                            'entries': result['entries'],
                            'total': total_songs,
                            'ydl_opts': ydl_opts
                        }
                        await update.message.reply_text(
                            self.get_text(user_id, "playlist_warning").format(total_songs),
                            reply_markup=reply_markup
                        )
                        return
                    else:
                        print("[DEBUG] Detectada canción individual")
                        self.active_downloads[user_id] = True
                        ydl_opts = {
                            'format': 'bestaudio/best',
                            'postprocessors': [{
                                'key': 'FFmpegExtractAudio',
                                'preferredcodec': 'mp3',
                                'preferredquality': self.user_settings[user_id]["quality"],
                            }],
                            'extract_flat': False,
                            'add_metadata': True,
                            'writethumbnail': True,
                            'noplaylist': True
                        }
                        with YoutubeDL(ydl_opts) as ydl:
                            single_info = ydl.extract_info(url, download=False)
                        await self._handle_single_song(update, context, single_info, ydl_opts)
                
            except Exception as ydl_error:
                error_message = str(ydl_error).lower()
                if "private video" in error_message:
                    await update.message.reply_text(
                        "❌ Este video es privado y no se puede descargar."
                    )
                elif "video unavailable" in error_message:
                    await update.message.reply_text(
                        "❌ Este video no está disponible o fue eliminado."
                    )
                elif "copyright" in error_message:
                    await update.message.reply_text(
                        "❌ Este video no se puede descargar por restricciones de copyright."
                    )
                else:
                    await update.message.reply_text(
                        f"❌ Error al procesar el video: {str(ydl_error)}"
                    )
                print(f"[DEBUG] Error en yt-dlp: {ydl_error}")
                
        except Exception as e:
            print(f"[DEBUG] Error en handle_url: {str(e)}")
            await update.message.reply_text(
                "❌ Ha ocurrido un error inesperado. Por favor, intenta con otro enlace."
            )
        finally:
            if not is_playlist:
                self.active_downloads[user_id] = False

    async def search_in_vault(self, context: ContextTypes.DEFAULT_TYPE, video_id: str) -> Optional[str]:
        """Busca una canción en la bóveda usando el video_id."""
        connection = get_db_connection()
        if connection is None:
            return None
        
        try:
            cursor = connection.cursor()
            query = "SELECT file_id FROM vault WHERE video_id = %s"
            cursor.execute(query, (video_id,))
            result = cursor.fetchone()
            if result:
                return result[0]  # Retorna file_id si se encuentra
        except Exception as e:
            print(f"Error buscando en bóveda: {e}")
        finally:
            cursor.close()
            connection.close()
        
        return None
    
    async def save_to_vault(self, video_id: str, file_id: str, title: str, artist: str):
        """Guarda la canción en la bóveda usando MySQL."""
        connection = get_db_connection()
        if connection is None:
            return
        
        try:
            cursor = connection.cursor()
            query = """
            INSERT INTO vault (video_id, file_id, title, artist)
            VALUES (%s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE file_id = %s
            """
            cursor.execute(query, (video_id, file_id, title, artist, file_id))
            connection.commit()
        except Exception as e:
            print(f"Error guardando en bóveda: {e}")
        finally:
            cursor.close()
            connection.close()

    async def download_song(self, url, ydl_opts):
        """Función para descargar canciones en un hilo separado"""
        loop = asyncio.get_event_loop()
        with YoutubeDL(ydl_opts) as ydl:
            return await loop.run_in_executor(
                self.thread_pool,
                partial(ydl.download, [url])
            )

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10))
    async def _download_with_retry(self, url, ydl_opts):
        """Función con reintentos automáticos para descargas"""
        return await self.download_song(url, ydl_opts)

    async def send_large_audio(self, context, chat_id, audio_path, **kwargs):
        """Envía archivos de audio grandes en chunks con reintentos"""
        max_retries = self.MAX_RETRIES
        retry_count = 0
        
        while retry_count < max_retries:
            try:
                async with aiofiles.open(audio_path, 'rb') as file:
                    return await context.bot.send_audio(
                        chat_id=chat_id,
                        audio=audio_path,
                        **kwargs,
                        read_timeout=30,
                        write_timeout=30,
                        connect_timeout=30,
                        pool_timeout=30
                    )
            except TimedOut:
                retry_count += 1
                if retry_count == max_retries:
                    raise
                await asyncio.sleep(2 ** retry_count)  # Espera exponencial
            except Exception as e:
                print(f"[DEBUG] Error inesperado al enviar audio: {e}")
                raise

    async def _handle_single_song(self, update: Update, context: ContextTypes.DEFAULT_TYPE, info: dict, ydl_opts: dict):
        user_id = update.effective_user.id
        files_created = []
        
        # Verificar si estamos procesando una playlist
        is_playlist = 'playlist_info' in context.user_data
        
        # Verificar cache
        cache_key = f"{info['id']}_{self.user_settings[user_id]['quality']}"
        if cache_key in self.download_cache:
            print("[DEBUG] Usando versión cacheada de la canción")
            cached_file_id = self.download_cache[cache_key]
            await context.bot.send_audio(
                chat_id=update.effective_chat.id,
                audio=cached_file_id
            )
            return

        print(f"[DEBUG] Iniciando _handle_single_song para usuario {user_id}")
        
        chat_id = update.effective_chat.id
        
        # Solo crear mensaje de status si NO es parte de una playlist
        status_message = None
        if not is_playlist:
            status_message = await context.bot.send_message(
                chat_id=chat_id,
                text=self.get_text(user_id, "searching_vault")
            )

        try:
            # Buscar en la bóveda usando MySQL
            video_id = info['id']
            print(f"[DEBUG] Buscando video_id {video_id} en la bóveda")
            file_id = await self.search_in_vault(context, video_id)
            
            if file_id:
                print(f"[DEBUG] Canción encontrada en la bóveda con file_id: {file_id}")
                try:
                    if not is_playlist:
                        await status_message.edit_text(self.get_text(user_id, "found_in_vault"))
                    # Validar el file_id antes de usarlo
                    file = await context.bot.get_file(file_id)
                    await context.bot.send_audio(
                        chat_id=chat_id,
                        audio=file.file_id,
                        title=info['title'],
                        performer=info.get('artist', info.get('uploader', 'Unknown')),
                        caption=f"🎵 {info['title']}"
                    )
                    if status_message is not None and not is_playlist:
                        complete_message = await status_message.edit_text(self.get_text(user_id, "download_complete"))
                        # Programar eliminación del mensaje después de 10 segundos
                        async def delete_complete_message():
                            await asyncio.sleep(10)
                            try:
                                await complete_message.delete()
                            except Exception as e:
                                print(f"[DEBUG] Error al eliminar mensaje de completado: {e}")
                        asyncio.create_task(delete_complete_message())
                    print("[DEBUG] Canción enviada desde la bóveda exitosamente")
                    self.download_cache[cache_key] = file.file_id
                    return  # Retornamos directamente si se envió desde la bóveda
                except Exception as e:
                    print(f"[DEBUG] Error al reenviar desde bóveda: {e}")
            
            # Si no está en la bóveda o falló el reenvío, descargar
            print("[DEBUG] Iniciando descarga de nueva canción")
            if not is_playlist and status_message is not None:
                await status_message.edit_text(self.get_text(user_id, "downloading"))
            
            # Sanitizar el nombre del archivo
            safe_title = self.sanitize_filename(info['title'])
            mp3_filename = f"{safe_title}.mp3"
            files_created.extend([
                mp3_filename,
                f"{safe_title}.webp",
                f"{safe_title}.jpg"
            ])
            print(f"[DEBUG] Nombre sanitizado del archivo: {safe_title}")
            
            ydl_opts = {
                'format': 'bestaudio/best',
                'writethumbnail': True,
                'outtmpl': f'{safe_title}.%(ext)s',
                'postprocessors': [{
                    'key': 'FFmpegExtractAudio',
                    'preferredcodec': 'mp3',
                    'preferredquality': self.user_settings[user_id]["quality"],
                }, {
                    'key': 'FFmpegMetadata',
                    'add_metadata': True,
                }, {
                    'key': 'EmbedThumbnail',
                }],
                'embedthumbnail': True,
                'updatetime': False,
                'ignoreerrors': True,  # Agregar esta opción
                'no_warnings': True,   # Agregar esta opción
                'extract_flat': False  # Agregar esta opción
            }
            
            print("[DEBUG] Iniciando descarga con yt-dlp")
            # Usar el nuevo método de descarga threaded
            try:
                await self._download_with_retry(info['webpage_url'], ydl_opts)
            except Exception as e:
                print(f"[DEBUG] Error después de reintentos: {e}")
                raise
            print("[DEBUG] Descarga completada con yt-dlp")
            
            # Preparar nombres de archivo
            mp3_filename = f"{safe_title}.mp3"
            print(f"[DEBUG] Nombre final del archivo MP3: {mp3_filename}")
            
            try:
                # Después de la descarga exitosa del archivo
                metadata_enabled = self.user_settings[user_id].get('metadata', False)
                
                if metadata_enabled:
                    print("[DEBUG] Obteniendo metadatos de la canción")
                    metadata = await self.get_track_metadata(info['title'], info)
                    
                    if metadata:
                        print("[DEBUG] Aplicando metadatos al archivo")
                        cover_path = await self.apply_metadata(mp3_filename, metadata)
                        
                        # Actualizar la información para el envío
                        info['title'] = metadata['title']
                        info['artist'] = metadata['artist']
                        
                        if cover_path:
                            files_created.append(cover_path)
                
                # Continuar con el envío del archivo...
                
            except Exception as e:
                print(f"[DEBUG] Error en el procesamiento de metadatos: {e}")
                # Continuar con el envío del archivo sin metadatos
                
            # Enviar a la bóveda primero
            print("[DEBUG] Enviando archivo a la bóveda")
            if status_message is not None and not is_playlist:
                await status_message.edit_text(self.get_text(user_id, "saving_to_vault"))
            
            caption = (
                f"🎵 {info['title']}\n"
                f"👤 {info.get('artist', info.get('uploader', 'Unknown'))}\n"
                f"youtube_id:{video_id}"
            )
            
            try:
                # Enviar a la bóveda con reintentos
                vault_message = await self.send_large_audio(
                    context,
                    self.VAULT_CHAT_ID,
                    mp3_filename,
                    title=info['title'],
                    performer=info.get('artist', info.get('uploader', 'Unknown')),
                    duration=info.get('duration'),
                    caption=caption
                )
                
                print("[DEBUG] Guardando información en base de datos")
                await self.save_to_vault(
                    video_id, 
                    vault_message.audio.file_id, 
                    info['title'], 
                    info.get('artist', info.get('uploader', 'Unknown'))
                )
                
                # Enviar al usuario usando el mismo file_id
                await context.bot.send_audio(
                    chat_id=update.effective_chat.id,
                    audio=vault_message.audio.file_id,  # Usar file_id en lugar de reenviar archivo
                    title=info['title'],
                    performer=info.get('artist', info.get('uploader', 'Unknown')),
                    duration=info.get('duration')
                )
                
            except TimedOut as e:
                print(f"[DEBUG] Timeout al enviar archivo: {e}")
                # Intentar enviar directamente al usuario si falló la bóveda
                user_message = await self.send_large_audio(
                    context,
                    update.effective_chat.id,
                    mp3_filename,
                    title=info['title'],
                    performer=info.get('artist', info.get('uploader', 'Unknown')),
                    duration=info.get('duration')
                )
                raise e  # Re-lanzar para manejo superior
                
            if status_message is not None and not is_playlist:
                complete_message = await status_message.edit_text(self.get_text(user_id, "download_complete"))
                # Programar eliminación del mensaje después de 10 segundos
                async def delete_complete_message():
                    await asyncio.sleep(10)
                    try:
                        await complete_message.delete()
                    except Exception as e:
                        print(f"[DEBUG] Error al eliminar mensaje de completado: {e}")
                asyncio.create_task(delete_complete_message())
            print("[DEBUG] Proceso completado exitosamente")
            
        except Exception as send_error:
            print(f"[DEBUG] Error al enviar archivo: {send_error}")
            if not isinstance(send_error, TimedOut):
                raise send_error
            
        except Exception as e:
            print(f"[DEBUG] Error general en _handle_single_song: {e}")
            if status_message is not None and not is_playlist:
                error_message = await status_message.edit_text(self.get_text(user_id, "download_error").format(str(e)))
                # También eliminar el mensaje de error después de 10 segundos
                async def delete_error_message():
                    await asyncio.sleep(10)
                    try:
                        await error_message.delete()
                    except Exception as e:
                        print(f"[DEBUG] Error al eliminar mensaje de error: {e}")
                asyncio.create_task(delete_error_message())
        
        finally:
            # Solo realizar limpieza si se crearon archivos
            if files_created:
                try:
                    print("[DEBUG] Iniciando limpieza de archivos descargados")
                    await asyncio.sleep(1)
                    for file in files_created:
                        if os.path.exists(file):
                            try:
                                os.remove(file)
                                print(f"[DEBUG] Archivo eliminado: {file}")
                            except Exception as e:
                                print(f"[DEBUG] Error al eliminar {file}: {e}")
                except Exception as e:
                    print(f"[DEBUG] Error durante la limpieza: {e}")

    async def _handle_playlist(self, update: Update, context: ContextTypes.DEFAULT_TYPE, info: dict, ydl_opts: dict):
        BATCH_SIZE = 5  # Procesar 5 canciones simultáneamente
        
        async def process_batch(batch):
            tasks = []
            for entry in batch:
                # Construir la URL completa usando el ID del video
                video_url = f"https://www.youtube.com/watch?v={entry['id']}"
                
                # Obtener información completa del video antes de procesarlo
                try:
                    with YoutubeDL({'extract_flat': False}) as ydl:
                        video_info = ydl.extract_info(video_url, download=False)
                        task = self._handle_single_song(update, context, video_info, ydl_opts)
                        tasks.append(task)
                except Exception as e:
                    print(f"[DEBUG] Error al obtener información del video {entry.get('title', 'Unknown')}: {e}")
                    continue
                
            return await asyncio.gather(*tasks, return_exceptions=True)

        playlist_entries = info['entries']
        total_songs = len(playlist_entries)

        try:
            # Procesar en lotes
            for i in range(0, total_songs, BATCH_SIZE):
                batch = playlist_entries[i:i + BATCH_SIZE]
                await process_batch(batch)
                
                # Actualizar solo el mensaje de progreso
                if 'progress_message' in context.user_data:
                    await context.user_data['progress_message'].edit_text(
                        f" Descargando...\nProgreso: {min(i + BATCH_SIZE, total_songs)}/{total_songs}"
                    )
        except Exception as e:
            print(f"[DEBUG] Error en _handle_playlist: {e}")
            raise
        finally:
            # La limpieza del mensaje se maneja en el callback_handler
            pass

    def run(self, token: str):
        app = Application.builder().token(token).build()

        
        # Registrar handlers
        app.add_handler(CommandHandler("start", self.start))
        app.add_handler(CommandHandler("cancel", self.cancel_download))
        app.add_handler(CommandHandler("donar", self.donar_command))  # Nuevo
        app.add_handler(CommandHandler("configuracion", self.configuracion_command))  # Nuevo
        app.add_handler(MessageHandler(filters.TEXT & filters.Entity("url"), self.handle_url))
        app.add_handler(CallbackQueryHandler(self.callback_handler))
        
        # Iniciar el bot
        app.run_polling()

    async def get_track_metadata(self, title: str, info: dict) -> dict:
        """Busca y obtiene los metadatos de una canción usando múltiples estrategias de búsqueda."""
        try:
            print("\n=== INICIO DE BÚSQUEDA DE METADATOS ===")
            print(f"[DEBUG] Título original: {title}")
            
            # 1. Extraer artista de los metadatos de YouTube
            artist = None
            if 'artist' in info:
                artist = info['artist']
            elif 'creator' in info:
                artist = info['creator']
            elif 'uploader' in info:
                artist = info['uploader']
            print(f"[DEBUG] Artista de metadatos: {artist}")

            # 2. PRIMER FILTRO: Buscar por título después del guion
            print("\n[DEBUG] === INICIANDO PRIMER FILTRO: TÍTULO DESPUÉS DEL GUION ===")
            if ' - ' in title:
                split_title = title.split(' - ')
                potential_artist = split_title[0].strip()
                potential_title = split_title[1].strip()
                
                # Limpiar el título extraído
                clean_split_title = re.sub(r'\(.*?\)|\[.*?\]|Official.*?Video|Lyrics?|HD|HQ|Official|Video|Audio', '', potential_title, flags=re.IGNORECASE).strip()
                print(f"[DEBUG] Título extraído después del guion: {clean_split_title}")
                print(f"[DEBUG] Artista extraído antes del guion: {potential_artist}")
                
                # Intentar búsqueda con estos datos
                query = f'recording:"{clean_split_title}" AND artist:"{potential_artist}"'
                print(f"[DEBUG] Búsqueda del primer filtro: {query}")
                result = musicbrainzngs.search_recordings(query=query, limit=5)
                
                if result.get('recording-list'):
                    print("[DEBUG] ✅ Resultados encontrados con el primer filtro")
                    return await self._process_results(result, clean_split_title, potential_artist)

            # 3. SEGUNDO FILTRO: Búsqueda por título y artista de metadatos
            print("\n[DEBUG] === INICIANDO SEGUNDO FILTRO: TÍTULO Y ARTISTA DE METADATOS ===")
            if artist:
                # Limpiar título y artista
                clean_title = re.sub(r'\(.*?\)|\[.*?\]|Official.*?Video|Lyrics?|HD|HQ|Official|Video|Audio', '', title, flags=re.IGNORECASE).strip()
                clean_artist = re.sub(r'\(.*?\)|\[.*?\]|VEVO|Official|Channel|Topic', '', artist, flags=re.IGNORECASE)
                clean_artist = clean_artist.split(',')[0].split('feat.')[0].split('ft.')[0].split('&')[0].strip()
                
                print(f"[DEBUG] Título limpio: {clean_title}")
                print(f"[DEBUG] Artista limpio: {clean_artist}")
                
                query = f'recording:"{clean_title}" AND artist:"{clean_artist}"'
                print(f"[DEBUG] Búsqueda del segundo filtro: {query}")
                result = musicbrainzngs.search_recordings(query=query, limit=5)
                
                if result.get('recording-list'):
                    print("[DEBUG] ✅ Resultados encontrados con el segundo filtro")
                    return await self._process_results(result, clean_title, clean_artist)

            # 4. TERCER FILTRO: Búsqueda solo por título
            print("\n[DEBUG] === INICIANDO TERCER FILTRO: SOLO TÍTULO ===")
            clean_title = re.sub(r'\(.*?\)|\[.*?\]|Official.*?Video|Lyrics?|HD|HQ|Official|Video|Audio', '', title, flags=re.IGNORECASE).strip()
            query = f'recording:"{clean_title}"'
            print(f"[DEBUG] Búsqueda del tercer filtro: {query}")
            result = musicbrainzngs.search_recordings(query=query, limit=5)
            
            if result.get('recording-list'):
                print("[DEBUG] ✅ Resultados encontrados con el tercer filtro")
                return await self._process_results(result, clean_title, None)
            
            print("[DEBUG] ❌ No se encontraron coincidencias en ningún filtro")
            return None

        except Exception as e:
            print(f"\n[DEBUG] ❌ Error general en get_track_metadata: {e}")
            print("=== FIN DE BÚSQUEDA DE METADATOS CON ERROR ===")
            return None

    async def _process_results(self, result: dict, clean_title: str, artist: Optional[str]) -> dict:
        """Procesa los resultados de la búsqueda y obtiene los metadatos detallados."""
        try:
            print("\n[DEBUG] Procesando resultados de búsqueda...")
            print(f"[DEBUG] 🎯 Se encontraron {len(result['recording-list'])} resultados")
            
            # Mostrar todos los resultados encontrados
            for i, recording in enumerate(result['recording-list'], 1):
                print(f"\n--- Resultado {i} ---")
                print(f"Título: {recording.get('title', 'No disponible')}")
                print(f"Artista: {recording.get('artist-credit-phrase', 'No disponible')}")
                if 'release-list' in recording:
                    print(f"Álbum: {recording['release-list'][0]['title']}")
                print(f"ID: {recording['id']}")
            
            # Obtener información detallada del primer resultado
            recording = result['recording-list'][0]
            recording_id = recording['id']
            
            print(f"\n[DEBUG] Obteniendo detalles del recording ID: {recording_id}")
            full_info = musicbrainzngs.get_recording_by_id(recording_id, includes=['artists', 'releases'])
            
            # Construir metadata
            metadata = {
                'title': recording.get('title', clean_title),
                'artist': recording.get('artist-credit-phrase', artist or 'Unknown Artist'),
                'album': None,
                'year': None,
                'genre': None,
                'cover_url': None
            }
            
            # Obtener información del álbum y carátula
            if 'release-list' in full_info['recording']:
                release = full_info['recording']['release-list'][0]
                metadata['album'] = release['title']
                metadata['year'] = release['date'][:4] if 'date' in release else None
                
                try:
                    print("[DEBUG] Buscando carátula del álbum...")
                    cover_art = musicbrainzngs.get_image_list(release['id'])
                    if cover_art['images']:
                        front_images = [img for img in cover_art['images'] if img.get('front', False)]
                        if front_images:
                            metadata['cover_url'] = front_images[0]['image']
                            print("[DEBUG] ✅ Carátula encontrada")
                except Exception as e:
                    print(f"[DEBUG] Error al obtener carátula: {e}")
            
            print("\n[DEBUG] Metadatos finales:")
            for key, value in metadata.items():
                print(f"{key}: {value}")
            
            return metadata
            
        except Exception as e:
            print(f"[DEBUG] Error al procesar resultados: {e}")
            return None

    async def apply_metadata(self, mp3_path: str, metadata: dict) -> Optional[str]:
        """
        Aplica los metadatos al archivo MP3 y retorna la ruta de la carátula si se descargó.
        Returns:
            Optional[str]: Ruta del archivo de carátula o None si no hay carátula
        """
        try:
            print("\n=== INICIO DE APLICACIÓN DE METADATOS ===")
            # Crear o cargar las etiquetas ID3
            audio = MP3(mp3_path, ID3=ID3)
            
            if metadata is None:
                print("[DEBUG] No hay metadatos para aplicar")
                return None
            
            # Asegurarse de que existe una etiqueta ID3
            if audio.tags is None:
                audio.add_tags()
            
            # Aplicar metadatos básicos
            print("[DEBUG] Aplicando metadatos básicos...")
            audio.tags.add(TIT2(encoding=3, text=metadata['title']))
            audio.tags.add(TPE1(encoding=3, text=metadata['artist']))
            if metadata.get('album'):
                audio.tags.add(TALB(encoding=3, text=metadata['album']))
            if metadata.get('year'):
                audio.tags.add(TDRC(encoding=3, text=metadata['year']))
            if metadata.get('genre'):
                audio.tags.add(TCON(encoding=3, text=metadata['genre']))
            
            # Manejar la carátula
            cover_path = None
            if metadata.get('cover_url'):
                try:
                    print("[DEBUG] Descargando carátula del álbum...")
                    # Crear nombre único para la carátula
                    cover_path = f"{os.path.splitext(mp3_path)[0]}_cover.jpg"
                    
                    # Descargar la imagen
                    response = requests.get(metadata['cover_url'])
                    response.raise_for_status()
                    
                    # Guardar la imagen localmente
                    with open(cover_path, 'wb') as img_file:
                        img_file.write(response.content)
                    print(f"[DEBUG] Carátula guardada en: {cover_path}")
                    
                    # Leer la imagen para incrustarla
                    with open(cover_path, 'rb') as img_file:
                        img_data = img_file.read()
                    
                    # Eliminar carátulas existentes
                    for key in list(audio.tags.keys()):
                        if key.startswith('APIC:'):
                            del audio.tags[key]
                    
                    # Agregar la nueva carátula
                    print("[DEBUG] Incrustando carátula en el MP3...")
                    audio.tags.add(
                        APIC(
                            encoding=3,
                            mime='image/jpeg',
                            type=3,  # 3 es para la carátula frontal
                            desc='Cover',
                            data=img_data
                        )
                    )
                    
                    # Verificar que la carátula se incrustó correctamente
                    audio.save()
                    verify_audio = ID3(mp3_path)
                    if any(key.startswith('APIC:') for key in verify_audio.keys()):
                        print("[DEBUG] ✅ Carátula incrustada correctamente")
                    else:
                        print("[DEBUG] ⚠️ Advertencia: No se detectó la carátula después de incrustarla")
                    
                except Exception as e:
                    print(f"[DEBUG] ❌ Error al procesar la carátula: {e}")
                    if cover_path and os.path.exists(cover_path):
                        os.remove(cover_path)
                    cover_path = None
            
            # Guardar los cambios finales
            audio.save(v2_version=3)  # Forzar versión ID3v2.3 para mejor compatibilidad
            print("[DEBUG] ✅ Metadatos aplicados exitosamente")
            
            return cover_path
            
        except Exception as e:
            print(f"[DEBUG] ❌ Error al aplicar metadatos: {e}")
            return None

if __name__ == "__main__":
    from config import TELEGRAM_TOKEN, VAULT_CHATID
    
    bot = MelodifyBot()
    bot.run(TELEGRAM_TOKEN)