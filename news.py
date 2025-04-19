# %%
import sys
import logging
import requests
from bs4 import BeautifulSoup
from typing import List, Dict, Any
from PyQt5 import QtWidgets, QtGui, QtCore

# グローバルなHTTPセッション（接続再利用のため）
session = requests.Session()

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

def parse_finance_news(html: str) -> List[Dict[str, Any]]:
    """
    HTML文字列からニュース記事のタイトルとサブデータを抽出する。
    """
    soup = BeautifulSoup(html, "html.parser")
    results: List[Dict[str, Any]] = []
    for a_tag in soup.find_all("a", href=True):
        data_span = a_tag.find("span", class_="data__2rwG")
        if data_span:
            title_tag = data_span.find("span", class_="title__36K6")
            if title_tag:
                title = title_tag.get_text(strip=True)
                sub_data_tags = data_span.find_all("span", class_="subData__1gx5")
                sub_data = " ".join(tag.get_text(strip=True) for tag in sub_data_tags)
                results.append({"title": title, "subData": sub_data})
    return results

def scrape_finance_news() -> List[Dict[str, Any]]:
    """
    Yahoo!ファイナンスのニュースページから記事情報を取得する。
    """
    url = "https://finance.yahoo.co.jp/news/new"
    try:
        with session.get(url, timeout=10) as response:
            response.raise_for_status()
            html = response.text
    except requests.RequestException as e:
        logging.error("Error fetching %s: %s", url, e)
        return []
    return parse_finance_news(html)

def scrape_stock_data() -> str:
    """
    Yahoo!ファイナンスの株情報ページから情報を取得し、整形済み１行テキストとして返す。
    """
    url = "https://finance.yahoo.co.jp/stocks/us/ranking/marketCapital"
    try:
        response = session.get(url, timeout=10)
        response.raise_for_status()
    except requests.RequestException as e:
        logging.error("Error fetching %s: %s", url, e)
        return ""
    soup = BeautifulSoup(response.text, "html.parser")
    table = soup.find("table", class_="UsStockRankingList__table__32ax")
    if not table:
        logging.error("対象のテーブルが見つかりません")
        return ""
    rows = []
    tbodies = table.find_all("tbody")
    if tbodies:
        for tbody in tbodies:
            rows.extend(tbody.find_all("tr"))
    else:
        rows = table.find_all("tr")
    ticker_data_list = []
    for row in rows:
        # ティッカー
        ticker_elements = row.find_all("li", class_="UsStockRankingList__supplement__2yWf")
        ticker = ticker_elements[0].get_text(strip=True) if ticker_elements and len(ticker_elements) > 0 else ""
        # 取引値と前日比
        spans = row.find_all("span", class_="StyledNumber__value__3rXW")
        trading_value = spans[0].get_text(strip=True) if spans and len(spans) >= 1 else ""
        day_change = spans[2].get_text(strip=True) if spans and len(spans) >= 3 else ""
        if day_change and not day_change.endswith("%"):
            day_change += "%"
        formatted = f"{ticker} {trading_value}（{day_change}）"
        ticker_data_list.append(formatted)
    output_line = "／".join(ticker_data_list)
    return output_line

# 非同期処理用ワーカー（ニュース）
class FetchNewsWorker(QtCore.QObject):
    finished = QtCore.pyqtSignal(str)
    error = QtCore.pyqtSignal(str)

    @QtCore.pyqtSlot()
    def run(self):
        try:
            news_list = scrape_finance_news()
            # 各記事を "◆タイトル - サブデータ" の形式で連結
            new_text = "".join(f"◆{news['title']} - {news['subData']}" for news in news_list)
        except Exception as e:
            logging.exception("Exception in FetchNewsWorker")
            self.error.emit(str(e))
            return
        self.finished.emit(new_text)

# 非同期処理用ワーカー（株情報）
class FetchStockWorker(QtCore.QObject):
    finished = QtCore.pyqtSignal(str)
    error = QtCore.pyqtSignal(str)

    @QtCore.pyqtSlot()
    def run(self):
        try:
            stock_text = scrape_stock_data()
        except Exception as e:
            logging.exception("Exception in FetchStockWorker")
            self.error.emit(str(e))
            return
        self.finished.emit(stock_text)

class DualTickerWidget(QtWidgets.QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        # ニュースと株情報のテキストとスクロール用オフセット
        self.news_text = ""
        self.stock_text = ""
        self.news_offset = 0.0
        self.stock_offset = 0.0

        # スクロール更新間隔（8ms）およびスクロール速度設定
        self.timer_interval = 8
        self.news_scroll_speed = 15  # ニュース記事のスクロール速度（px/update）
        self.stock_scroll_speed = self.news_scroll_speed / 2  # 株情報は3分の1の速度

        # スクロール更新タイマー
        self.scroll_timer = QtCore.QTimer(self)
        self.scroll_timer.timeout.connect(self.update_offsets)
        self.scroll_timer.start(self.timer_interval)

        # 15分ごとのデータ更新タイマー
        self.fetch_timer = QtCore.QTimer(self)
        self.fetch_timer.timeout.connect(self.fetch_all_data)
        self.fetch_timer.start(900000)  # 900,000ms = 15分

        # 起動時に初期データ取得
        self.fetch_all_data()

        # 背景色と文字色（初期：背景黒、文字白）
        self.bg_color = QtCore.Qt.black
        self.text_color = QtCore.Qt.white
        self.setAutoFillBackground(True)
        self.update_background()

        # フォント設定（60px、游明朝）
        self.font_size = 60  
        self.font = QtGui.QFont("Noto Sans CJK JP", self.font_size)
        self.setFont(self.font)

    def update_background(self):
        """
        現在の背景色に合わせてウィジェットのパレットを更新する。
        """
        palette = self.palette()
        palette.setColor(QtGui.QPalette.Window, self.bg_color)
        self.setPalette(palette)

    def update_offsets(self):
        """
        ニュースと株情報それぞれの描画オフセットを更新し、再描画を行う。
        """
        fm = QtGui.QFontMetrics(self.font)
        # ニュース記事のオフセット更新
        news_text_width = fm.horizontalAdvance(self.news_text)
        self.news_offset -= self.news_scroll_speed
        if abs(self.news_offset) > news_text_width:
            self.news_offset = 0
        # 株情報のオフセット更新（速度はニュースの4分の1）
        stock_text_width = fm.horizontalAdvance(self.stock_text)
        self.stock_offset -= self.stock_scroll_speed
        if abs(self.stock_offset) > stock_text_width:
            self.stock_offset = 0
        self.update()

    def fetch_all_data(self):
        """
        ニュース記事と株情報の両方を取得する。
        """
        self.fetch_news_text()
        self.fetch_stock_text()

    def fetch_news_text(self):
        """
        ニュース記事を取得する非同期処理を開始する。
        """
        self.news_thread = QtCore.QThread()
        self.news_worker = FetchNewsWorker()
        self.news_worker.moveToThread(self.news_thread)
        self.news_thread.started.connect(self.news_worker.run)
        self.news_worker.finished.connect(self.on_fetch_news_finished)
        self.news_worker.finished.connect(self.news_thread.quit)
        self.news_worker.finished.connect(self.news_worker.deleteLater)
        self.news_thread.finished.connect(self.news_thread.deleteLater)
        self.news_worker.error.connect(self.on_fetch_error)
        self.news_worker.error.connect(self.news_thread.quit)
        self.news_thread.start()

    def fetch_stock_text(self):
        """
        株情報を取得する非同期処理を開始する。
        """
        self.stock_thread = QtCore.QThread()
        self.stock_worker = FetchStockWorker()
        self.stock_worker.moveToThread(self.stock_thread)
        self.stock_thread.started.connect(self.stock_worker.run)
        self.stock_worker.finished.connect(self.on_fetch_stock_finished)
        self.stock_worker.finished.connect(self.stock_thread.quit)
        self.stock_worker.finished.connect(self.stock_worker.deleteLater)
        self.stock_thread.finished.connect(self.stock_thread.deleteLater)
        self.stock_worker.error.connect(self.on_fetch_error)
        self.stock_worker.error.connect(self.stock_thread.quit)
        self.stock_thread.start()

    def on_fetch_news_finished(self, new_text: str):
        """
        ニュース記事取得完了時のコールバック。取得テキストを更新する。
        """
        if new_text:
            self.news_text = new_text
            self.news_offset = 0

    def on_fetch_stock_finished(self, new_text: str):
        """
        株情報取得完了時のコールバック。取得テキストを更新する。
        """
        if new_text:
            self.stock_text = new_text
            self.stock_offset = 0

    def on_fetch_error(self, error_str: str):
        logging.error("Fetch error: %s", error_str)

    def paintEvent(self, event):
        """
        ウィジェットの背景と、２行のスクロールテキスト（ニュース記事・株情報）を描画する。
        """
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        painter.fillRect(self.rect(), self.bg_color)
        painter.setPen(QtGui.QPen(self.text_color))
        painter.setFont(self.font)
        fm = QtGui.QFontMetrics(self.font)

        # １行目：ニュース記事の描画
        news_text_width = fm.horizontalAdvance(self.news_text)
        news_center = self.height() / 4
        news_y = int(news_center + (fm.ascent() - fm.height() / 2))
        news_x = int(self.news_offset)
        painter.drawText(news_x, news_y, self.news_text)
        painter.drawText(news_x + news_text_width, news_y, self.news_text)

        # ２行目：株情報の描画
        stock_text_width = fm.horizontalAdvance(self.stock_text)
        stock_center = 3 * self.height() / 4
        stock_y = int(stock_center + (fm.ascent() - fm.height() / 2))
        stock_x = int(self.stock_offset)
        painter.drawText(stock_x, stock_y, self.stock_text)
        painter.drawText(stock_x + stock_text_width, stock_y, self.stock_text)

class MainWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Finance News and Stock Ticker")
        self.setFixedSize(1920, 420)
        self.ticker_widget = DualTickerWidget(self)
        self.setCentralWidget(self.ticker_widget)
        self.fullscreen = False

    def keyPressEvent(self, event):
        """
        F11: フルスクリーン/ウィンドウ切替  
        F12: 背景色と文字色の反転（背景黒⇔背景白、文字色も反転）
        """
        if event.key() == QtCore.Qt.Key_F12:
            if self.ticker_widget.bg_color == QtCore.Qt.black:
                self.ticker_widget.bg_color = QtCore.Qt.white
                self.ticker_widget.text_color = QtCore.Qt.black
            else:
                self.ticker_widget.bg_color = QtCore.Qt.black
                self.ticker_widget.text_color = QtCore.Qt.white
            self.ticker_widget.update_background()
            self.ticker_widget.update()
        elif event.key() == QtCore.Qt.Key_F11:
            if self.fullscreen:
                self.showNormal()
                self.fullscreen = False
                self.setFixedSize(1920, 480)
            else:
                self.showFullScreen()
                self.fullscreen = True

def main():
    app = QtWidgets.QApplication(sys.argv)
    window = MainWindow()
    window.show()
    exit_code = app.exec_()
    session.close()  # アプリ終了時にHTTPセッションを明示的に閉じる
    sys.exit(exit_code)

if __name__ == "__main__":
    main()



