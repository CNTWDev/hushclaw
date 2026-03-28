---
name: Russia Smartphone Market Intel via Google News RSS
description: Auto-promoted from memory (recalled 29x)
author: hushclaw-auto
version: "1.0.0"
---

Fetches Russian smartphone market news via Google News RSS (Russian locale) for market intelligence reports on brands like Tecno/Infinix targeting Russia/CIS.


## Skill: Russia Smartphone Market Intelligence via Google News RSS

### Working RSS Endpoints (tested March 2026)
```
# General market + price trends
https://news.google.com/rss/search?q=смартфон+рынок+Россия+2026&hl=ru-RU&gl=RU&ceid=RU:ru

# Competitor brands (Xiaomi, Huawei)
https://news.google.com/rss/search?q=Xiaomi+Huawei+Россия+смартфон+2026&hl=ru-RU&gl=RU&ceid=RU:ru

# Consumer sentiment / trends
https://news.google.com/rss/search?q=покупка+смартфон+Россия+потребитель+2026&hl=ru-RU&gl=RU&ceid=RU:ru
```

### Key Russian Tech Sources (appear in feed)
- **ixbt.com**: Most detailed product launches, sales data, buyer guides
- **CNews.ru**: Breaking news, market data, security issues
- **DGL.RU**: Market analysis, annual rankings
- **the-geek.ru**: New product launches in Russia
- **AndroidInsider.ru**: Consumer advice, top lists, brand popularity
- **Mobile-review.com**: Deep reviews, editorial
- **Forbes.ru**: Price warnings, economic analysis

### Report Structure
1. Fetch general market RSS → market size, volume trends, pricing
2. Fetch competitor RSS → brand rankings, new launches
3. Synthesize: market dynamics, hot models, consumer needs, brand opportunities

### Key Signals to Watch
- Price increase warnings (spring cycles typically Jan-Feb announcements)
- MTC / М.Видео holiday sales data (released mid-Jan)
- Government import policy changes (ixbt.com usually reports)
- 5G compatibility issues with Russian networks
- Mandatory app preinstall requirements (российский софт)
