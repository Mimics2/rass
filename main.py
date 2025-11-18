# main.py - основной файл бота
import asyncio
import logging
from telethon import TelegramClient, events
from telethon.tl.types import User
import pandas as pd
import os
from datetime import datetime
import random
import sqlite3
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Конфигурация
API_ID = 39123927
API_HASH = 'e4395ce4c701ce5524192b0e1f96e7a5'
BOT_TOKEN = '8269402325:AAEqO5c2n1C_t1iYOhEcMVg9JK0isIPguOw'  # Замените на токен вашего бота

# Инициализация клиента
client = TelegramClient('bot_session', API_ID, API_HASH)
scheduler = AsyncIOScheduler()

# База данных для хранения состояния
DB_FILE = 'mass_sender.db'

def init_db():
    """Инициализация базы данных"""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    # Таблица для пользователей
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            last_name TEXT,
            is_bot INTEGER,
            scraped_date TEXT,
            source_chat TEXT
        )
    ''')
    
    # Таблица для отправленных сообщений
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS sent_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            username TEXT,
            sent_date TEXT,
            message TEXT,
            status TEXT,
            attempts INTEGER DEFAULT 0,
            FOREIGN KEY (user_id) REFERENCES users (user_id)
        )
    ''')
    
    # Таблица для черновиков
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS drafts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            text TEXT,
            created_date TEXT
        )
    ''')
    
    # Таблица для активных рассылок
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS campaigns (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            draft_id INTEGER,
            status TEXT,
            started_date TEXT,
            completed_date TEXT,
            total_users INTEGER,
            sent_count INTEGER DEFAULT 0,
            failed_count INTEGER DEFAULT 0,
            FOREIGN KEY (draft_id) REFERENCES drafts (id)
        )
    ''')
    
    conn.commit()
    conn.close()

class MassSenderBot:
    def __init__(self):
        self.active_campaigns = {}
        self.max_attempts = 5
        self.base_delay = 30
        
    async def save_draft(self, text, user_id):
        """Сохранение черновика"""
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute(
            'INSERT INTO drafts (text, created_date) VALUES (?, ?)',
            (text, datetime.now().isoformat())
        )
        draft_id = cursor.lastrowid
        conn.commit()
        conn.close()
        
        await client.send_message(user_id, f"✅ Черновик сохранен (ID: {draft_id})")
        return draft_id
    
    async def list_drafts(self, user_id):
        """Показать список черновиков"""
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute('SELECT id, text, created_date FROM drafts ORDER BY id DESC')
        drafts = cursor.fetchall()
        conn.close()
        
        if not drafts:
            await client.send_message(user_id, "📝 Черновиков нет")
            return
        
        message = "📝 Ваши черновики:\n\n"
        for draft_id, text, created_date in drafts:
            preview = text[:50] + "..." if len(text) > 50 else text
            message += f"🆔 {draft_id}: {preview}\n📅 {created_date[:10]}\n\n"
        
        await client.send_message(user_id, message)
    
    async def add_users_from_chat(self, chat_link, user_id):
        """Добавление пользователей из чата"""
        try:
            chat = await client.get_entity(chat_link)
            users_data = []
            
            async for user in client.iter_participants(chat, aggressive=True, limit=10000):
                if user.username and not user.bot:
                    users_data.append((
                        user.id, user.username,
                        user.first_name or '', user.last_name or '',
                        int(user.bot), datetime.now().isoformat(),
                        getattr(chat, 'title', 'Unknown')
                    ))
            
            conn = sqlite3.connect(DB_FILE)
            cursor = conn.cursor()
            
            added_count = 0
            for user_data in users_data:
                try:
                    cursor.execute('''
                        INSERT OR IGNORE INTO users 
                        (user_id, username, first_name, last_name, is_bot, scraped_date, source_chat)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                    ''', user_data)
                    if cursor.rowcount > 0:
                        added_count += 1
                except:
                    continue
            
            conn.commit()
            
            # Получаем общее количество
            cursor.execute('SELECT COUNT(*) FROM users')
            total_users = cursor.fetchone()[0]
            conn.close()
            
            await client.send_message(
                user_id, 
                f"✅ Добавлено {added_count} новых пользователей\n"
                f"📊 Всего в базе: {total_users}"
            )
            
        except Exception as e:
            await client.send_message(user_id, f"❌ Ошибка: {str(e)}")
    
    async def start_campaign(self, draft_id, user_id):
        """Запуск рассылки"""
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        
        # Получаем текст черновика
        cursor.execute('SELECT text FROM drafts WHERE id = ?', (draft_id,))
        draft = cursor.fetchone()
        
        if not draft:
            await client.send_message(user_id, "❌ Черновик не найден")
            conn.close()
            return
        
        message_text = draft[0]
        
        # Получаем пользователей, которым еще не отправляли это сообщение
        cursor.execute('''
            SELECT u.user_id, u.username 
            FROM users u 
            LEFT JOIN sent_messages sm ON u.user_id = sm.user_id AND sm.message = ?
            WHERE sm.id IS NULL AND u.is_bot = 0
        ''', (message_text,))
        
        users_to_send = cursor.fetchall()
        
        if not users_to_send:
            await client.send_message(user_id, "❌ Нет пользователей для отправки")
            conn.close()
            return
        
        # Создаем кампанию
        cursor.execute('''
            INSERT INTO campaigns (draft_id, status, started_date, total_users)
            VALUES (?, ?, ?, ?)
        ''', (draft_id, 'running', datetime.now().isoformat(), len(users_to_send)))
        
        campaign_id = cursor.lastrowid
        conn.commit()
        conn.close()
        
        self.active_campaigns[campaign_id] = {
            'status': 'running',
            'current_index': 0,
            'users': users_to_send,
            'message_text': message_text,
            'success_count': 0,
            'failed_count': 0
        }
        
        # Запускаем рассылку в фоне
        asyncio.create_task(self.run_campaign(campaign_id, user_id))
        
        await client.send_message(
            user_id,
            f"🚀 Запущена рассылка #{campaign_id}\n"
            f"📝 Сообщение: {message_text[:50]}...\n"
            f"👥 Пользователей: {len(users_to_send)}\n"
            f"⏳ Начинаем отправку..."
        )
    
    async def run_campaign(self, campaign_id, admin_id):
        """Выполнение рассылки с повторными попытками"""
        campaign = self.active_campaigns[campaign_id]
        users = campaign['users']
        message_text = campaign['message_text']
        
        conn = sqlite3.connect(DB_FILE)
        
        for i, (user_id, username) in enumerate(users[campaign['current_index']:], 
                                              campaign['current_index'] + 1):
            if campaign['status'] != 'running':
                break
                
            success = await self.send_with_retry(user_id, username, message_text, conn)
            
            if success:
                campaign['success_count'] += 1
            else:
                campaign['failed_count'] += 1
            
            campaign['current_index'] = i
            
            # Обновляем прогресс каждые 10 отправок
            if i % 10 == 0:
                await self.update_progress(campaign_id, admin_id, i, len(users))
            
            # Задержка между отправками
            delay = random.randint(self.base_delay, self.base_delay + 30)
            await asyncio.sleep(delay)
        
        # Завершаем кампанию
        cursor = conn.cursor()
        cursor.execute('''
            UPDATE campaigns 
            SET status = 'completed', completed_date = ?, sent_count = ?, failed_count = ?
            WHERE id = ?
        ''', (
            datetime.now().isoformat(),
            campaign['success_count'],
            campaign['failed_count'],
            campaign_id
        ))
        conn.commit()
        conn.close()
        
        await self.send_final_report(campaign_id, admin_id)
        self.active_campaigns.pop(campaign_id, None)
    
    async def send_with_retry(self, user_id, username, message_text, conn):
        """Отправка с повторными попытками"""
        for attempt in range(self.max_attempts):
            try:
                await client.send_message(username, message_text)
                
                # Сохраняем в историю
                cursor = conn.cursor()
                cursor.execute('''
                    INSERT INTO sent_messages (user_id, username, sent_date, message, status, attempts)
                    VALUES (?, ?, ?, ?, ?, ?)
                ''', (
                    user_id, username, datetime.now().isoformat(), 
                    message_text, 'success', attempt + 1
                ))
                conn.commit()
                
                logger.info(f"✅ Отправлено @{username} (попытка {attempt + 1})")
                return True
                
            except Exception as e:
                error_msg = str(e)
                logger.warning(f"❌ Попытка {attempt + 1} для @{username}: {error_msg}")
                
                if "FLOOD_WAIT" in error_msg:
                    try:
                        wait_time = int(error_msg.split()[-1])
                        await asyncio.sleep(wait_time)
                    except:
                        await asyncio.sleep(60)
                elif any(x in error_msg for x in ["USERNAME_NOT_OCCUPIED", "USER_BLOCKED"]):
                    # Бесполезно повторять для этих ошибок
                    break
                else:
                    await asyncio.sleep(30 * (attempt + 1))  # Увеличивающаяся задержка
        
        # Если все попытки неудачны
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO sent_messages (user_id, username, sent_date, message, status, attempts)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (
            user_id, username, datetime.now().isoformat(), 
            message_text, 'failed', self.max_attempts
        ))
        conn.commit()
        
        return False
    
    async def update_progress(self, campaign_id, admin_id, current, total):
        """Обновление прогресса"""
        campaign = self.active_campaigns[campaign_id]
        progress = (current / total) * 100
        
        await client.send_message(
            admin_id,
            f"📊 Рассылка #{campaign_id}\n"
            f"📈 Прогресс: {current}/{total} ({progress:.1f}%)\n"
            f"✅ Успешно: {campaign['success_count']}\n"
            f"❌ Ошибок: {campaign['failed_count']}"
        )
    
    async def send_final_report(self, campaign_id, admin_id):
        """Отправка финального отчета"""
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute(
            'SELECT sent_count, failed_count FROM campaigns WHERE id = ?', 
            (campaign_id,)
        )
        sent_count, failed_count = cursor.fetchone()
        conn.close()
        
        await client.send_message(
            admin_id,
            f"🎉 Рассылка #{campaign_id} завершена!\n"
            f"✅ Успешно отправлено: {sent_count}\n"
            f"❌ Не удалось отправить: {failed_count}\n"
            f"📊 Эффективность: {(sent_count/(sent_count+failed_count))*100:.1f}%"
        )
    
    async def get_stats(self, user_id):
        """Получение статистики"""
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        
        cursor.execute('SELECT COUNT(*) FROM users')
        total_users = cursor.fetchone()[0]
        
        cursor.execute('SELECT COUNT(*) FROM sent_messages WHERE status = "success"')
        total_sent = cursor.fetchone()[0]
        
        cursor.execute('SELECT COUNT(*) FROM campaigns')
        total_campaigns = cursor.fetchone()[0]
        
        cursor.execute('SELECT COUNT(*) FROM drafts')
        total_drafts = cursor.fetchone()[0]
        
        conn.close()
        
        await client.send_message(
            user_id,
            f"📊 Статистика бота:\n"
            f"👥 Пользователей в базе: {total_users}\n"
            f"📤 Отправлено сообщений: {total_sent}\n"
            f"📝 Проведено рассылок: {total_campaigns}\n"
            f"📄 Сохранено черновиков: {total_drafts}"
        )

# Инициализация бота
bot = MassSenderBot()

@client.on(events.NewMessage(pattern='/start'))
async def start_handler(event):
    """Обработчик команды /start"""
    user = await event.get_sender()
    await event.reply(
        f"👋 Привет, {user.first_name}!\n\n"
        "Я бот для массовой рассылки в Telegram.\n\n"
        "📋 Доступные команды:\n"
        "/add_chat - Добавить пользователей из чата\n"
        "/save_draft - Сохранить черновик\n" 
        "/list_drafts - Показать черновики\n"
        "/start_campaign - Запустить рассылку\n"
        "/stats - Статистика\n"
        "/stop_campaign - Остановить рассылку\n"
        "/help - Помощь"
    )

@client.on(events.NewMessage(pattern='/add_chat'))
async def add_chat_handler(event):
    """Добавление пользователей из чата"""
    async with client.conversation(event.chat_id) as conv:
        await conv.send_message("Введите ссылку на чат/канал:")
        chat_link = await conv.get_response()
        
        await conv.send_message("🔄 Начинаем сбор пользователей...")
        await bot.add_users_from_chat(chat_link.text, event.chat_id)

@client.on(events.NewMessage(pattern='/save_draft'))
async def save_draft_handler(event):
    """Сохранение черновика"""
    async with client.conversation(event.chat_id) as conv:
        await conv.send_message("Введите текст сообщения:")
        text = await conv.get_response()
        
        await bot.save_draft(text.text, event.chat_id)

@client.on(events.NewMessage(pattern='/list_drafts'))
async def list_drafts_handler(event):
    """Список черновиков"""
    await bot.list_drafts(event.chat_id)

@client.on(events.NewMessage(pattern='/start_campaign'))
async def start_campaign_handler(event):
    """Запуск рассылки"""
    async with client.conversation(event.chat_id) as conv:
        await conv.send_message("Введите ID черновика:")
        draft_id_msg = await conv.get_response()
        
        try:
            draft_id = int(draft_id_msg.text)
            await bot.start_campaign(draft_id, event.chat_id)
        except ValueError:
            await conv.send_message("❌ Неверный ID черновика")

@client.on(events.NewMessage(pattern='/stats'))
async def stats_handler(event):
    """Статистика"""
    await bot.get_stats(event.chat_id)

@client.on(events.NewMessage(pattern='/stop_campaign'))
async def stop_campaign_handler(event):
    """Остановка рассылки"""
    # Реализация остановки активной рассылки
    await event.reply("🛑 Функция остановки в разработке")

@client.on(events.NewMessage(pattern='/help'))
async def help_handler(event):
    """Помощь"""
    await event.reply(
        "📖 Помощь по боту:\n\n"
        "1. Сначала добавьте пользователей: /add_chat\n"
        "2. Сохраните сообщение: /save_draft\n" 
        "3. Запустите рассылку: /start_campaign\n\n"
        "⚙️ Бот автоматически повторяет отправку при ошибках\n"
        "⏱ Задержки между сообщениями для избежания блокировки\n"
        "📊 Подробная статистика отправки"
    )

async def main():
    """Основная функция"""
    init_db()
    logger.info("🚀 Запуск бота массовой рассылки...")
    
    # Запуск планировщика
    scheduler.start()
    
    # Запуск бота
    await client.start(bot_token=BOT_TOKEN)
    logger.info("✅ Бот запущен и готов к работе")
    
    await client.run_until_disconnected()

if __name__ == '__main__':
    asyncio.run(main())
