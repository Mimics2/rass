# main.py
import asyncio
import logging
import sqlite3
import random
import os
import re
from datetime import datetime
from telethon import TelegramClient, events
from telethon.tl.types import User

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Конфигурация из переменных окружения
API_ID = int(os.getenv('API_ID', '39123927'))
API_HASH = os.getenv('API_HASH', 'e4395ce4c701ce5524192b0e1f96e7a5')
BOT_TOKEN = os.getenv('BOT_TOKEN', '')

# Проверка обязательных переменных
if not BOT_TOKEN:
    logger.error("❌ BOT_TOKEN не установлен! Задайте его в переменных окружения.")
    exit(1)

# Инициализация клиента
client = TelegramClient('mass_sender_bot', API_ID, API_HASH)

class DatabaseManager:
    def __init__(self, db_file='mass_sender.db'):
        self.db_file = db_file
        self.init_db()
    
    def init_db(self):
        """Инициализация базы данных"""
        conn = sqlite3.connect(self.db_file)
        cursor = conn.cursor()
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                last_name TEXT,
                is_bot INTEGER DEFAULT 0,
                scraped_date TEXT,
                source_chat TEXT,
                is_active INTEGER DEFAULT 1
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS drafts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                text TEXT,
                created_date TEXT
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS sent_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                username TEXT,
                sent_date TEXT,
                message TEXT,
                status TEXT,
                attempts INTEGER DEFAULT 0,
                error_message TEXT
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS campaigns (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                draft_id INTEGER,
                status TEXT DEFAULT 'running',
                started_date TEXT,
                completed_date TEXT,
                total_users INTEGER,
                sent_count INTEGER DEFAULT 0,
                failed_count INTEGER DEFAULT 0,
                current_index INTEGER DEFAULT 0
            )
        ''')
        
        conn.commit()
        conn.close()
        logger.info("✅ База данных инициализирована")
    
    def execute_query(self, query, params=()):
        """Выполнение запроса"""
        conn = sqlite3.connect(self.db_file)
        cursor = conn.cursor()
        try:
            cursor.execute(query, params)
            conn.commit()
            return cursor
        except Exception as e:
            logger.error(f"❌ Ошибка БД: {e}")
            raise
        finally:
            conn.close()
    
    def fetch_all(self, query, params=()):
        """Получение всех результатов"""
        conn = sqlite3.connect(self.db_file)
        cursor = conn.cursor()
        try:
            cursor.execute(query, params)
            return cursor.fetchall()
        finally:
            conn.close()
    
    def fetch_one(self, query, params=()):
        """Получение одного результата"""
        conn = sqlite3.connect(self.db_file)
        cursor = conn.cursor()
        try:
            cursor.execute(query, params)
            return cursor.fetchone()
        finally:
            conn.close()

class MassSenderBot:
    def __init__(self):
        self.db = DatabaseManager()
        self.active_campaigns = {}
        self.base_delay_min = 45
        self.base_delay_max = 60
    
    def is_valid_username(self, username):
        """Проверка валидности username"""
        if not username or not isinstance(username, str):
            return False
        
        # Убираем @ в начале если есть
        username = username.lstrip('@')
        
        # Проверяем длину (5-32 символа для Telegram)
        if len(username) < 5 or len(username) > 32:
            return False
        
        # Проверяем допустимые символы (только буквы, цифры, подчеркивания)
        if not re.match(r'^[a-zA-Z0-9_]+$', username):
            return False
        
        return True
    
    async def save_draft(self, text, user_id):
        """Сохранение черновика"""
        try:
            self.db.execute_query(
                'INSERT INTO drafts (text, created_date) VALUES (?, ?)',
                (text, datetime.now().isoformat())
            )
            draft_id = self.db.fetch_one('SELECT last_insert_rowid()')[0]
            
            await client.send_message(
                user_id, 
                f"✅ Черновик сохранен!\nID: {draft_id}"
            )
            return draft_id
        except Exception as e:
            await client.send_message(user_id, f"❌ Ошибка сохранения: {e}")
    
    async def list_drafts(self, user_id):
        """Показать список черновиков"""
        try:
            drafts = self.db.fetch_all(
                'SELECT id, text, created_date FROM drafts ORDER BY id DESC'
            )
            
            if not drafts:
                await client.send_message(user_id, "📝 Черновиков нет")
                return
            
            message = "📝 Ваши черновики:\n\n"
            for draft_id, text, created_date in drafts:
                preview = text[:50] + "..." if len(text) > 50 else text
                message += f"🆔 {draft_id}: {preview}\n"
                message += f"📅 {created_date[:16]}\n\n"
            
            await client.send_message(user_id, message)
        except Exception as e:
            await client.send_message(user_id, f"❌ Ошибка: {e}")
    
    async def add_users_from_chat(self, chat_link, user_id):
        """Добавление пользователей из чата"""
        try:
            await client.send_message(user_id, "🔄 Получаем информацию о чате...")
            chat = await client.get_entity(chat_link)
            
            await client.send_message(
                user_id, 
                f"🎯 Чат: {getattr(chat, 'title', 'Unknown')}\n"
                f"🔄 Начинаем сбор пользователей..."
            )
            
            users_added = 0
            invalid_users = 0
            
            async for user in client.iter_participants(chat, limit=500):
                if user.username:
                    # Проверяем валидность username
                    if not self.is_valid_username(user.username):
                        invalid_users += 1
                        continue
                    
                    # Пропускаем ботов
                    if user.bot:
                        continue
                    
                    try:
                        self.db.execute_query('''
                            INSERT OR IGNORE INTO users 
                            (user_id, username, first_name, last_name, is_bot, scraped_date, source_chat, is_active)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        ''', (
                            user.id, user.username,
                            user.first_name or '',
                            user.last_name or '',
                            0,
                            datetime.now().isoformat(),
                            getattr(chat, 'title', 'Unknown'),
                            1  # is_active
                        ))
                        users_added += 1
                    except Exception as e:
                        continue
            
            total_users = self.db.fetch_one('SELECT COUNT(*) FROM users WHERE is_active = 1')[0] or 0
            
            await client.send_message(
                user_id,
                f"✅ Сбор завершен!\n"
                f"👥 Добавлено новых: {users_added}\n"
                f"🚫 Пропущено невалидных: {invalid_users}\n"
                f"📊 Всего активных в базе: {total_users}"
            )
            
        except Exception as e:
            await client.send_message(
                user_id, 
                f"❌ Ошибка: {str(e)[:200]}"
            )
    
    async def start_campaign(self, draft_id, user_id):
        """Запуск рассылки"""
        try:
            draft = self.db.fetch_one(
                'SELECT text FROM drafts WHERE id = ?', 
                (draft_id,)
            )
            
            if not draft:
                await client.send_message(user_id, "❌ Черновик не найден")
                return
            
            message_text = draft[0]
            
            # Получаем только активных пользователей с валидными username
            users = self.db.fetch_all('''
                SELECT u.user_id, u.username 
                FROM users u
                WHERE u.is_bot = 0 
                AND u.is_active = 1
                AND u.user_id NOT IN (
                    SELECT user_id FROM sent_messages WHERE message = ? AND status = 'success'
                )
            ''', (message_text,))
            
            if not users:
                await client.send_message(user_id, "❌ Нет пользователей для отправки")
                return
            
            # Дополнительная фильтрация валидных username
            valid_users = []
            for user_id, username in users:
                if self.is_valid_username(username):
                    valid_users.append((user_id, username))
                else:
                    # Помечаем пользователя как неактивного
                    self.db.execute_query(
                        'UPDATE users SET is_active = 0 WHERE user_id = ?',
                        (user_id,)
                    )
            
            if not valid_users:
                await client.send_message(user_id, "❌ Нет валидных пользователей для отправки")
                return
            
            self.db.execute_query('''
                INSERT INTO campaigns (draft_id, started_date, total_users, current_index)
                VALUES (?, ?, ?, ?)
            ''', (draft_id, datetime.now().isoformat(), len(valid_users), 0))
            
            campaign_id = self.db.fetch_one('SELECT last_insert_rowid()')[0]
            
            self.active_campaigns[campaign_id] = {
                'status': 'running',
                'admin_id': user_id
            }
            
            asyncio.create_task(self.run_campaign(campaign_id, user_id, valid_users, message_text))
            
            await client.send_message(
                user_id,
                f"🚀 Рассылка #{campaign_id} запущена!\n"
                f"👥 Валидных получателей: {len(valid_users)}\n"
                f"⏳ Задержка: {self.base_delay_min}-{self.base_delay_max} сек\n"
                f"🛑 Для остановки: /stop_campaign {campaign_id}"
            )
            
        except Exception as e:
            await client.send_message(user_id, f"❌ Ошибка запуска: {e}")
    
    async def run_campaign(self, campaign_id, admin_id, users, message_text):
        """Выполнение рассылки"""
        success_count = 0
        failed_count = 0
        
        for i, (user_id, username) in enumerate(users, 1):
            try:
                # Проверяем не остановлена ли кампания
                if campaign_id not in self.active_campaigns:
                    break
                    
                if self.active_campaigns[campaign_id]['status'] == 'stopped':
                    logger.info(f"🛑 Рассылка {campaign_id} остановлена")
                    break
                
                # Обновляем текущий индекс
                self.db.execute_query(
                    'UPDATE campaigns SET current_index = ? WHERE id = ?',
                    (i, campaign_id)
                )
                
                # Пытаемся отправить сообщение (ОДНА попытка)
                sent = await self.send_message(user_id, username, message_text)
                
                if sent:
                    success_count += 1
                    logger.info(f"✅ [{i}/{len(users)}] Отправлено @{username}")
                else:
                    failed_count += 1
                    logger.info(f"❌ [{i}/{len(users)}] Пропущен @{username}")
                
                # Обновляем статистику
                self.db.execute_query('''
                    UPDATE campaigns SET sent_count = ?, failed_count = ? WHERE id = ?
                ''', (success_count, failed_count, campaign_id))
                
                # Прогресс каждые 10 сообщений
                progress_interval = 10
                if i % progress_interval == 0 or i == len(users):
                    await self.update_progress(
                        campaign_id, admin_id, i, len(users), 
                        success_count, failed_count
                    )
                
                # Задержка только между сообщениями (45-60 секунд)
                delay = random.randint(self.base_delay_min, self.base_delay_max)
                logger.info(f"⏳ Ожидание {delay} секунд...")
                await asyncio.sleep(delay)
                
            except Exception as e:
                logger.error(f"❌ Критическая ошибка: {e}")
                failed_count += 1
                continue
        
        # Завершаем кампанию
        campaign_status = 'completed'
        if campaign_id in self.active_campaigns:
            if self.active_campaigns[campaign_id]['status'] == 'stopped':
                campaign_status = 'stopped'
            self.active_campaigns.pop(campaign_id)
        
        self.db.execute_query('''
            UPDATE campaigns 
            SET status = ?, completed_date = ?, sent_count = ?, failed_count = ?
            WHERE id = ?
        ''', (campaign_status, datetime.now().isoformat(), success_count, failed_count, campaign_id))
        
        await self.send_final_report(campaign_id, admin_id, success_count, failed_count, len(users))
    
    async def send_message(self, user_id, username, message_text):
        """Отправка сообщения (ОДНА попытка как в рабочем коде)"""
        try:
            # Дополнительная проверка username перед отправкой
            if not self.is_valid_username(username):
                logger.warning(f"🚫 Невалидный username: @{username}")
                self.db.execute_query(
                    'UPDATE users SET is_active = 0 WHERE user_id = ?',
                    (user_id,)
                )
                return False
            
            # ОДНА попытка отправки - как в вашем рабочем коде
            await client.send_message(username, message_text)
            
            # Сохраняем успешную отправку
            self.db.execute_query('''
                INSERT INTO sent_messages 
                (user_id, username, sent_date, message, status, attempts)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (
                user_id, username, datetime.now().isoformat(),
                message_text, 'success', 1
            ))
            
            return True
            
        except Exception as e:
            error_msg = str(e)
            logger.warning(f"❌ Ошибка отправки @{username}: {error_msg}")
            
            # Обработка ошибок как в вашем рабочем коде
            if any(x in error_msg for x in ["Invalid peer", "USERNAME_NOT_OCCUPIED", "USER_BLOCKED", "CHAT_WRITE_FORBIDDEN", "PEER_FLOOD"]):
                # Помечаем пользователя как неактивного
                self.db.execute_query(
                    'UPDATE users SET is_active = 0 WHERE user_id = ?',
                    (user_id,)
                )
                logger.info(f"🚫 Пользователь @{username} помечен как неактивный")
            
            # Обработка FLOOD_WAIT - ждем указанное время
            elif "FLOOD_WAIT" in error_msg:
                try:
                    wait_time = int(error_msg.split()[-1])
                    logger.info(f"🚫 FLOOD_WAIT: ожидание {wait_time} секунд...")
                    await asyncio.sleep(wait_time)
                except:
                    logger.info("🚫 FLOOD_WAIT: ожидание 60 секунд...")
                    await asyncio.sleep(60)
                # После FLOOD_WAIT продолжаем со следующим пользователем
                return False
            
            # Сохраняем неудачную отправку
            self.db.execute_query('''
                INSERT INTO sent_messages 
                (user_id, username, sent_date, message, status, attempts, error_message)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (
                user_id, username, datetime.now().isoformat(),
                message_text, 'failed', 1, error_msg
            ))
            
            return False
    
    async def update_progress(self, campaign_id, admin_id, current, total, success, failed):
        """Обновление прогресса"""
        progress = (current / total) * 100
        try:
            await client.send_message(
                admin_id,
                f"📊 Рассылка #{campaign_id}\n"
                f"📈 Прогресс: {current}/{total} ({progress:.1f}%)\n"
                f"✅ Успешно: {success}\n"
                f"❌ Пропущено: {failed}"
            )
        except Exception as e:
            logger.error(f"Ошибка отправки прогресса: {e}")
    
    async def send_final_report(self, campaign_id, admin_id, success, failed, total):
        """Финальный отчет"""
        efficiency = (success / total) * 100 if total > 0 else 0
        
        try:
            await client.send_message(
                admin_id,
                f"🎉 Рассылка #{campaign_id} завершена!\n\n"
                f"✅ Успешно отправлено: {success}\n"
                f"❌ Пропущено пользователей: {failed}\n"
                f"📊 Эффективность: {efficiency:.1f}%"
            )
        except Exception as e:
            logger.error(f"Ошибка отправки финального отчета: {e}")
    
    async def get_stats(self, user_id):
        """Получение статистики"""
        try:
            total_users = self.db.fetch_one('SELECT COUNT(*) FROM users WHERE is_active = 1')[0] or 0
            total_sent = self.db.fetch_one(
                'SELECT COUNT(*) FROM sent_messages WHERE status = "success"'
            )[0] or 0
            total_campaigns = self.db.fetch_one('SELECT COUNT(*) FROM campaigns')[0] or 0
            total_drafts = self.db.fetch_one('SELECT COUNT(*) FROM drafts')[0] or 0
            
            await client.send_message(
                user_id,
                f"📊 Статистика бота:\n\n"
                f"👥 Активных пользователей: {total_users}\n"
                f"📤 Успешно отправлено: {total_sent}\n"
                f"📝 Проведено рассылок: {total_campaigns}\n"
                f"📄 Черновиков: {total_drafts}"
            )
        except Exception as e:
            await client.send_message(user_id, f"❌ Ошибка получения статистики: {e}")
    
    async def stop_campaign(self, campaign_id, user_id):
        """Остановка рассылки"""
        try:
            campaign_id = int(campaign_id)
            
            if campaign_id in self.active_campaigns:
                self.active_campaigns[campaign_id]['status'] = 'stopped'
                await client.send_message(user_id, f"🛑 Рассылка #{campaign_id} остановлена!")
            else:
                await client.send_message(user_id, f"❌ Рассылка #{campaign_id} не найдена")
                    
        except ValueError:
            await client.send_message(user_id, "❌ Неверный формат ID кампании")
        except Exception as e:
            await client.send_message(user_id, f"❌ Ошибка остановки: {e}")

# Инициализация бота
mass_bot = MassSenderBot()

@client.on(events.NewMessage(pattern='/start'))
async def start_handler(event):
    """Обработчик команды /start"""
    user = await event.get_sender()
    await event.reply(
        f"👋 Привет, {user.first_name}!\n\n"
        "Я бот для автоматической массовой рассылки.\n\n"
        "📋 Команды:\n"
        "/add_chat - Добавить пользователей\n"
        "/save_draft - Сохранить сообщение\n"
        "/list_drafts - Список сообщений\n"
        "/start_campaign - Начать рассылку\n"
        "/stats - Статистика\n"
        "/stop_campaign ID - Остановить\n"
        "/help - Помощь"
    )

@client.on(events.NewMessage(pattern='/help'))
async def help_handler(event):
    """Обработчик команды /help"""
    await event.reply(
        "📖 Инструкция:\n\n"
        "1. /add_chat - добавить пользователей\n"
        "2. /save_draft - сохранить текст\n"
        "3. /start_campaign - начать рассылку\n\n"
        "⚡ Особенности:\n"
        "• ОДНА попытка отправки на пользователя\n"
        "• Пропуск невалидных пользователей\n"
        "• Задержка 45-60 секунд между сообщениями\n"
        "• Автоматическая обработка ошибок"
    )

@client.on(events.NewMessage(pattern='/add_chat'))
async def add_chat_handler(event):
    """Добавление пользователей из чата"""
    async with client.conversation(event.chat_id, timeout=300) as conv:
        await conv.send_message("🔗 Пришлите ссылку на чат:")
        try:
            response = await conv.get_response()
            await mass_bot.add_users_from_chat(response.text, event.chat_id)
        except asyncio.TimeoutError:
            await conv.send_message("⏰ Время ожидания истекло")

@client.on(events.NewMessage(pattern='/save_draft'))
async def save_draft_handler(event):
    """Сохранение черновика"""
    async with client.conversation(event.chat_id, timeout=300) as conv:
        await conv.send_message("📝 Пришлите текст для рассылки:")
        try:
            response = await conv.get_response()
            await mass_bot.save_draft(response.text, event.chat_id)
        except asyncio.TimeoutError:
            await conv.send_message("⏰ Время ожидания истекло")

@client.on(events.NewMessage(pattern='/list_drafts'))
async def list_drafts_handler(event):
    """Список черновиков"""
    await mass_bot.list_drafts(event.chat_id)

@client.on(events.NewMessage(pattern='/start_campaign'))
async def start_campaign_handler(event):
    """Запуск рассылки"""
    async with client.conversation(event.chat_id, timeout=300) as conv:
        await conv.send_message("🔢 Введите ID черновика:")
        try:
            response = await conv.get_response()
            draft_id = int(response.text)
            await mass_bot.start_campaign(draft_id, event.chat_id)
        except ValueError:
            await conv.send_message("❌ Неверный формат ID")
        except asyncio.TimeoutError:
            await conv.send_message("⏰ Время ожидания истекло")

@client.on(events.NewMessage(pattern='/stats'))
async def stats_handler(event):
    """Статистика"""
    await mass_bot.get_stats(event.chat_id)

@client.on(events.NewMessage(pattern=r'/stop_campaign(\s+\d+)?'))
async def stop_campaign_handler(event):
    """Остановка рассылки"""
    command_parts = event.text.split()
    if len(command_parts) == 1:
        await event.reply("🛑 Укажите ID рассылки: /stop_campaign 1")
        return
    
    try:
        campaign_id = command_parts[1]
        await mass_bot.stop_campaign(campaign_id, event.chat_id)
    except Exception as e:
        await event.reply(f"❌ Ошибка: {e}")

async def main():
    """Основная функция"""
    try:
        logger.info("🚀 Запуск бота массовой рассылки...")
        await client.start(bot_token=BOT_TOKEN)
        
        me = await client.get_me()
        logger.info(f"✅ Бот @{me.username} запущен и готов к работе")
        
        await client.run_until_disconnected()
    except Exception as e:
        logger.error(f"❌ Критическая ошибка: {e}")

if __name__ == '__main__':
    asyncio.run(main())
