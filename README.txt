NEWS MARKET GAME (Updated)

Included updates:
- Professional dark UI
- Detailed news (headline + summary + full 'read more' modal)
- Company, sector, and multi-sector news supported
- Market moves with background noise + spillover effects
- Trading always enabled (buy/sell anytime)
- Quantity input does not reset to 1 when the page refreshes
- Direction/intensity/sector tags are NOT shown to players/presenter (hidden server-side)

Run:
1) pip install -r requirements.txt
2) python app.py
3) Open:
   - Player:     http://127.0.0.1:8000
   - Presenter:  http://127.0.0.1:8000/presenter
   - Admin:      http://127.0.0.1:8000/admin

Admin password:
- Change in config.py (default: admin123)
