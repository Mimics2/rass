# config.py
import os

# Конфигурация бота
API_ID = int(os.getenv('API_ID', 39123927))
API_HASH = os.getenv('API_HASH', 'e4395ce4c701ce5524192b0e1f96e7a5')
BOT_TOKEN = os.getenv('BOT_TOKEN', 'YOUR_BOT_TOKEN_HERE')

# Настройки рассылки
MAX_ATTEMPTS = 5
BASE_DELAY = 30
MAX_DELAY = 90

# База данных
DB_FILE = 'mass_sender.db'
