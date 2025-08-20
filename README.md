# CryptoLensBot

Modular Telegram bot built with [aiogram](https://github.com/aiogram/aiogram).

## Project structure
```
├── bot.py                 # entry point
├── handlers/              # bot event handlers
│   ├── start.py           # /start command and main menu
│   ├── deals.py           # deals logic
│   ├── reports.py         # reports and analytics
│   ├── sets.py            # setup related handlers
│   └── other.py           # misc handlers
├── states/
│   └── states.py          # FSM state definitions
├── keyboards/
│   └── keyboards.py       # reply and inline keyboards
├── utils/
│   ├── database.py        # database utilities
│   ├── charts.py          # chart generation helpers
│   └── helpers.py         # miscellaneous helpers
├── config.py              # configuration and tokens
└── requirements.txt       # project dependencies
```

Each module only imports the symbols it needs. Handlers are registered in
`bot.py`, keeping the entry point minimal.

## Running
1. Create a virtual environment and install dependencies from
   `requirements.txt`.
2. Provide your bot token in a `.env` file as `BOT_TOKEN`.
3. Run the bot:
   ```bash
   python bot.py
   ```
