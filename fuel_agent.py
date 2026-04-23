import requests
from bs4 import BeautifulSoup
import smtplib
import os
import csv
import re
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta

GMAIL_ADDRESS = os.environ['GMAIL_ADDRESS']
GMAIL_APP_PASSWORD = os.environ['GMAIL_APP_PASSWORD'].strip()
HISTORY_FILE = 'price_history.csv'

NETWORKS = {
    'WOG': 'wog',
    'OKKO': 'okko',
}

FUEL_LABELS = {
    'А-95': 'Бензин А-95',
    'ДП': 'Дизель',
}

def fetch_prices(network_slug):
    url = f'https://index.minfin.com.ua/ua/markets/fuel/tm/{network_slug}/'
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept-Language': 'uk-UA,uk;q=0.9',
        'Referer': 'https://index.minfin.com.ua/',
    }
    response = requests.get(url, headers=headers, timeout=15)
    response.encoding = 'utf-8'
    soup = BeautifulSoup(response.text, 'html.parser')

    prices = {}

    # Шукаємо всі рядки з цінами
    rows = soup.find_all('tr')
    for row in rows:
        cols = row.find_all('td')
        if len(cols) >= 2:
            fuel_name = cols[0].get_text(strip=True)
            price_text = cols[-1].get_text(strip=True)
            price_text = re.sub(r'[^\d.,]', '', price_text).replace(',', '.')
            for key, label in FUEL_LABELS.items():
                if key in fuel_name:
                    try:
                        prices[label] = float(price_text)
                    except ValueError:
                        pass

    # Якщо таблиця не знайдена — шукаємо ціни у тексті сторінки
    if not prices:
        text = soup.get_text()
        for key, label in FUEL_LABELS.items():
            pattern = rf'{re.escape(key)}[^\d]*(\d{{2,3}}[.,]\d{{1,2}})'
            match = re.search(pattern, text)
            if match:
                try:
                    prices[label] = float(match.group(1).replace(',', '.'))
                except ValueError:
                    pass

    return prices

def load_history():
    history = {}
    if not os.path.exists(HISTORY_FILE):
        return history
    with open(HISTORY_FILE, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            date = row['date']
            if date not in history:
                history[date] = {}
            key = f"{row['network']}_{row['fuel']}"
            history[date][key] = float(row['price'])
    return history

def save_prices(date_str, all_prices):
    file_exists = os.path.exists(HISTORY_FILE)
    with open(HISTORY_FILE, 'a', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=['date', 'network', 'fuel', 'price'])
        if not file_exists:
            writer.writeheader()
        for network, prices in all_prices.items():
            for fuel, price in prices.items():
                writer.writerow({
                    'date': date_str,
                    'network': network,
                    'fuel': fuel,
                    'price': price
                })

def get_week_stats(history, network, fuel):
    key = f"{network}_{fuel}"
    last_7 = sorted(history.keys())[-7:]
    result = []
    for d in last_7:
        if key in history.get(d, {}):
            result.append((d, history[d][key]))
    return result

def build_email(today_str, all_prices, history):
    lines = []
    lines.append(f"⛽ Ціни на паливо — {today_str}")
    lines.append("=" * 40)

    yesterday = (datetime.strptime(today_str, '%Y-%m-%d') - timedelta(days=1)).strftime('%Y-%m-%d')

    for fuel_label in ['Дизель', 'Бензин А-95']:
        lines.append(f"\n🔹 {fuel_label}")
        lines.append("-" * 30)

        for network in ['WOG', 'OKKO']:
            price = all_prices.get(network, {}).get(fuel_label)
            if price is None:
                lines.append(f"  {network}: дані недоступні")
                continue

            key = f"{network}_{fuel_label}"
            yesterday_price = history.get(yesterday, {}).get(key)
            if yesterday_price:
                diff = price - yesterday_price
                pct = (diff / yesterday_price) * 100
                arrow = "🔺" if diff > 0 else ("🔻" if diff < 0 else "➡️")
                change_str = f"{arrow} {diff:+.2f} грн ({pct:+.1f}%)"
            else:
                change_str = "— перший день"

            lines.append(f"  {network}: {price:.2f} грн/л  {change_str}")

        wog_p = all_prices.get('WOG', {}).get(fuel_label)
        okko_p = all_prices.get('OKKO', {}).get(fuel_label)
        if wog_p and okko_p:
            diff = wog_p - okko_p
            if abs(diff) < 0.01:
                lines.append(f"  📊 WOG = ОККО")
            elif diff < 0:
                lines.append(f"  📊 WOG дешевше на {abs(diff):.2f} грн/л")
            else:
                lines.append(f"  📊 ОККО дешевше на {abs(diff):.2f} грн/л")

        lines.append(f"\n  📅 Динаміка за тиждень:")
        for network in ['WOG', 'OKKO']:
            week = get_week_stats(history, network, fuel_label)
            if week:
                week_str = " → ".join([f"{d[5:]}: {p:.2f}" for d, p in week])
                lines.append(f"  {network}: {week_str}")

    lines.append("\n" + "=" * 40)
    lines.append("Дані: index.minfin.com.ua")
    return "\n".join(lines)

def send_email(subject, body):
    msg = MIMEMultipart()
    msg['From'] = GMAIL_ADDRESS
    msg['To'] = GMAIL_ADDRESS
    msg['Subject'] = subject
    msg.attach(MIMEText(body, 'plain', 'utf-8'))
    with smtplib.SMTP_SSL('smtp.gmail.com', 465) as server:
        server.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
        server.send_message(msg)

def main():
    today_str = datetime.now().strftime('%Y-%m-%d')
    print(f"Запуск агента: {today_str}")

    all_prices = {}
    for network, slug in NETWORKS.items():
        print(f"Парсинг {network}...")
        prices = fetch_prices(slug)
        print(f"  Отримано: {prices}")
        all_prices[network] = prices

    history = load_history()
    save_prices(today_str, all_prices)

    body = build_email(today_str, all_prices, history)
    print("\n--- PREVIEW ---")
    print(body)
    print("--- END ---\n")

    subject = f"⛽ Паливо {today_str}"
    send_email(subject, body)
    print("Email відправлено!")

if __name__ == '__main__':
    main()
