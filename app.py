from flask import Flask
import requests, threading, time

app = Flask(__name__)
price = "0"
lock = threading.Lock()


def update_price():
    global price
    while True:
        try:
            p = requests.get(
                "https://api.coingecko.com/api/v3/simple/price?ids=bitcoin&vs_currencies=usd",
                timeout=5
            ).json()["bitcoin"]["usd"]
            with lock: price = str(int(p))
        except: pass
        time.sleep(10)

@app.route('/price')
def get_price():
    return price

if __name__ == '__main__':
    threading.Thread(target=update_price, daemon=True).start()
    app.run(host='0.0.0.0', port=5000)