import asyncio
import logging
from datetime import datetime, time, timedelta
from typing import List, Dict
import pytz
from telethon import TelegramClient
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Конфигурация
BOT_TOKEN = "8269402325:AAEqO5c2n1C_t1iYOhEcMVg9JK0isIPguOw"  # Токен вашего бота от @BotFather
API_ID = 34926321
API_HASH = '3ce3de5ab33d2defac471e34d47662e2'
PHONE_NUMBER = '+77474314960'  # Ваш номер телефона

# Глобальное состояние
user_client = None
chats_list = []
is_active = False
scheduled_tasks = []

class UserAccountManager:
    """Управление пользовательским аккаунтом через Telethon"""
    
    def __init__(self, api_id: int, api_hash: str):
        self.client = TelegramClient('user_session', api_id, api_hash)
        self.is_connected = False
    
    async def start(self):
        """Запуск клиента пользователя"""
        await self.client.start(phone=PHONE_NUMBER)
        self.is_connected = True
        me = await self.client.get_me()
        logger.info(f"Пользовательский аккаунт запущен: {me.first_name}")
        return me
    
    async def send_message_to_chat(self, chat_entity, message: str):
        """Отправка сообщения в чат от имени пользователя"""
        try:
            await self.client.send_message(chat_entity, message)
            return True
        except Exception as e:
            logger.error(f"Ошибка отправки: {e}")
            return False
    
    async def get_chat_by_link(self, chat_link: str):
        """Получение чата по ссылке или username"""
        try:
            chat = await self.client.get_entity(chat_link)
            return chat
        except Exception as e:
            logger.error(f"Ошибка получения чата: {e}")
            return None

# Команды для бота
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /start"""
    instructions = """
🤖 **Бот управления рассылкой BaroHolog**

**Доступные команды:**

📝 `/add_chats` - Добавить чаты для рассылки
▶️ `/start_bot` - Запуск автоматической рассылки  
🛑 `/stop_bot` - Остановить рассылку
📊 `/status` - Проверить статус

**Расписание рассылки:**
⏰ 09:00 по Москве - первая публикация
⏰ 17:00 по Москве - вторая публикация

**Важно:** Сообщения отправляются от вашего личного аккаунта!
    """
    await update.message.reply_text(instructions)

async def add_chats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /add_chats"""
    global user_client, chats_list
    
    if not context.args:
        await update.message.reply_text(
            "📝 **Добавление чатов**\n\n"
            "Использование: `/add_chats @username1 @username2`\n\n"
            "Или: `/add_chats https://t.me/username`\n\n"
            "Пример: `/add_chats @my_channel @my_group`",
            parse_mode='Markdown'
        )
        return
    
    if not user_client or not user_client.is_connected:
        await update.message.reply_text("❌ Пользовательский аккаунт не подключен")
        return
    
    added_chats = []
    failed_chats = []
    
    for chat_link in context.args:
        try:
            chat = await user_client.get_chat_by_link(chat_link)
            if chat:
                chat_info = {
                    'id': chat.id,
                    'title': getattr(chat, 'title', 'Private Chat'),
                    'username': getattr(chat, 'username', None),
                    'entity': chat
                }
                
                # Проверяем, не добавлен ли уже
                if not any(c['id'] == chat_info['id'] for c in chats_list):
                    chats_list.append(chat_info)
                    added_chats.append(chat_info['title'])
                else:
                    failed_chats.append(f"{chat_link} (уже добавлен)")
            else:
                failed_chats.append(chat_link)
        except Exception as e:
            failed_chats.append(f"{chat_link} (ошибка: {str(e)})")
    
    response = ""
    if added_chats:
        response += f"✅ Добавлены чаты: {', '.join(added_chats)}\n"
    if failed_chats:
        response += f"❌ Не удалось добавить: {', '.join(failed_chats)}\n"
    
    response += f"📊 Всего чатов: {len(chats_list)}"
    await update.message.reply_text(response)

async def start_bot_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /start_bot"""
    global is_active, chats_list, user_client
    
    if not user_client or not user_client.is_connected:
        await update.message.reply_text("❌ Пользовательский аккаунт не подключен")
        return
        
    if not chats_list:
        await update.message.reply_text("❌ Сначала добавьте чаты с помощью /add_chats")
        return
        
    if is_active:
        await update.message.reply_text("❌ Рассылка уже активна")
        return
    
    is_active = True
    asyncio.create_task(setup_schedule())
    
    chat_names = "\n".join([f"• {chat['title']}" for chat in chats_list])
    
    await update.message.reply_text(
        f"✅ **Рассылка запущена!**\n\n"
        f"📊 Чатов для рассылки: {len(chats_list)}\n"
        f"⏰ Расписание: 09:00 и 17:00 по Москве\n"
        f"📢 Публикаций в день: 2\n\n"
        f"Чаты:\n{chat_names}"
    )

async def stop_bot_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Остановка рассылки"""
    global is_active
    
    if not is_active:
        await update.message.reply_text("❌ Рассылка и так не активна")
        return
        
    is_active = False
    for task in scheduled_tasks:
        task.cancel()
    scheduled_tasks.clear()
    
    await update.message.reply_text("🛑 Рассылка остановлена")

async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /status"""
    global is_active, chats_list, user_client
    
    user_status = "🟢 Подключен" if user_client and user_client.is_connected else "🔴 Не подключен"
    bot_status = "🟢 Активна" if is_active else "🔴 Не активна"
    
    status_message = (
        f"🤖 **Статус системы BaroHolog**\n\n"
        f"👤 Пользовательский аккаунт: {user_status}\n"
        f"📊 Рассылка: {bot_status}\n"
        f"👥 Чатов в списке: {len(chats_list)}\n"
        f"⏰ Время проверки: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
    )
    
    if chats_list:
        status_message += "\n📋 Чаты:\n" + "\n".join([f"• {chat['title']}" for chat in chats_list[:3]])
        if len(chats_list) > 3:
            status_message += f"\n... и еще {len(chats_list) - 3} чатов"
    
    await update.message.reply_text(status_message)

# Функции рассылки
async def setup_schedule():
    """Настройка расписания рассылки"""
    global scheduled_tasks
    
    # Очищаем предыдущие задачи
    for task in scheduled_tasks:
        task.cancel()
    scheduled_tasks.clear()
    
    # Создаем задачи для двух времен
    times = [time(9, 0), time(17, 0)]  # 09:00 и 17:00 по Москве
    
    for send_time in times:
        task = asyncio.create_task(schedule_sender(send_time))
        scheduled_tasks.append(task)

async def schedule_sender(send_time: time):
    """Планировщик рассылки для конкретного времени"""
    moscow_tz = pytz.timezone('Europe/Moscow')
    
    while is_active:
        try:
            now = datetime.now(moscow_tz)
            target_time = moscow_tz.localize(datetime.combine(now.date(), send_time))
            
            # Если время уже прошло сегодня, планируем на завтра
            if now > target_time:
                target_time += timedelta(days=1)
            
            wait_seconds = (target_time - now).total_seconds()
            logger.info(f"Следующая рассылка в {send_time} через {wait_seconds:.0f} секунд")
            
            # Ждем до времени рассылки
            await asyncio.sleep(wait_seconds)
            
            if is_active:
                await send_messages()
            
            # Ждем до следующего дня
            await asyncio.sleep(86400 - wait_seconds)
            
        except Exception as e:
            logger.error(f"Ошибка в планировщике: {e}")
            await asyncio.sleep(60)

async def send_messages():
    """Отправка сообщений во все чаты от имени пользователя"""
    global chats_list, is_active, user_client
    
    if not is_active or not chats_list or not user_client:
        return
        
    logger.info(f"Начало рассылки в {len(chats_list)} чатов")
    
    success_count = 0
    fail_count = 0
    
    for chat_info in chats_list:
        try:
            message_text = """
📢 **Рекламное сообщение BaroHolog** 📢

Ваше рекламное сообщение здесь...

✨ Преимущества:
• Высокое качество
• Быстрая доставка  
• Отличная поддержка

📞 Контакты: ваш контакт
            """
            
            success = await user_client.send_message_to_chat(
                chat_info['entity'], 
                message_text
            )
            
            if success:
                success_count += 1
                logger.info(f"Сообщение отправлено в {chat_info['title']}")
            else:
                fail_count += 1
                
            # Пауза между отправками
            await asyncio.sleep(3)
            
        except Exception as e:
            fail_count += 1
            logger.error(f"Ошибка отправки в {chat_info['title']}: {e}")
    
    # Отправляем отчет через бота
    report_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    report_message = (
        f"📊 **Отчет о рассылке**\n\n"
        f"⏰ Время: {report_time}\n"
        f"✅ Успешно: {success_count}\n"
        f"❌ Ошибок: {fail_count}\n"
        f"📊 Всего чатов: {len(chats_list)}"
    )
    
    # Здесь можно отправить отчет конкретному пользователю
    # Для примера отправляем в тот же чат где была команда
    # В реальности лучше хранить ID администратора

async def main():
    """Основная функция запуска"""
    global user_client
    
    # Инициализация пользовательского клиента
    user_client = UserAccountManager(API_ID, API_HASH)
    await user_client.start()
    
    # Инициализация бота
    bot_application = Application.builder().token(BOT_TOKEN).build()
    
    # Добавляем обработчики команд
    bot_application.add_handler(CommandHandler("start", start_command))
    bot_application.add_handler(CommandHandler("add_chats", add_chats_command))
    bot_application.add_handler(CommandHandler("start_bot", start_bot_command))
    bot_application.add_handler(CommandHandler("stop_bot", stop_bot_command))
    bot_application.add_handler(CommandHandler("status", status_command))
    
    # Запускаем бота
    logger.info("Бот запускается...")
    await bot_application.initialize()
    await bot_application.start()
    await bot_application.updater.start_polling()
    
    # Бесконечный цикл
    await asyncio.Future()

if __name__ == "__main__":
    asyncio.run(main())
