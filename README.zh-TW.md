# p2pipcam 替代伺服器

[English](README.md) | 繁體中文

一套不需要任何套件、可以自己架設的伺服器，用來取代已經停止服務的 `www.p2pipcam.com` 雲端。適用於舊款「P2P IP Cam Lite」/ I-Bt 比特數位 IBT-200CP 系列攝影機。

如果你手上 2010–2012 年的網路攝影機只會講舊版 TUTK UDP 協定，原廠雲端又已經關閉，只要把攝影機原本使用的主機名稱指向你自己的伺服器，就能把影像拿回來。

> [!WARNING]
> **不要相信我。如果你需要這個功能，請自己架設伺服器。**
> 雲端上運作的 `www.p2pipcam.com` 就是這套程式，但它**會記錄你的資訊**，而且整個協定**完全沒有加密**，任何人都可以偷看你的攝影機畫面。

## 檔案說明

| 檔案（`re/`） | 用途 |
|---|---|
| `p2p_protocol.py` | 封包標頭、訊息類型、query / hole 編碼與解碼 |
| `p2p_server.py` | UDP 伺服器（30000/30002/30003/30004），支援中繼與直連模式、管理面板、Prometheus 指標 |
| `fake_cloud.py` | 代替 `www.p2pipcam.com/api/userip.asp`（攝影機自動註冊） |
| `camera_state.py` | 唯讀讀取攝影機 UDP 10139 的狀態與雲端請求 |
| `p2p_client.py` | 參考用戶端：query、hole punching、HTTP |
| `download_geoip.py` | 選用：定期下載 MaxMind GeoLite2-Country CSV，用來顯示國家 |
| `selftest.py` | 不需要實體攝影機的端對端測試 |

通訊協定的完整說明在 [`notes/REPORT.md`](notes/REPORT.md)（英文）。

## 使用 Docker

```sh
cd deploy
cat > .env <<'EOF'
PUBLIC_HOST=cameras.example.com
MAXMIND_URL=https://USER:KEY@download.maxmind.com/geoip/databases/GeoLite2-Country-CSV/download?suffix=zip
EOF
docker compose up -d --build
```

接著用**一般的 DNS A 紀錄**，把攝影機韌體裡寫死的主機名稱（通常是 `www.p2pipcam.com`）指向你的伺服器，並開放以下連接埠：

| 連接埠 | 協定 | 用途 |
|---|---|---|
| 80 | TCP + UDP | 攝影機自動註冊（`/api/userip.asp`，也會用 UDP 直接送） |
| 30000 | UDP | TUTK 裝置狀態 |
| 30002 | UDP | 查詢裝置位置（`0x60` → `0x61`/`0x63`） |
| 30003 | UDP | Hole punching（`0x02`/`0x04` → `0x06`） |
| 30004 | UDP | 中繼通道（`0x64` → `0x66`） |

CDN 和反向代理**不會**轉送 UDP，所以 DNS 紀錄不能開 Cloudflare proxy。

## 不使用 Docker

```sh
cd re
sudo python3 fake_cloud.py --port 80 --body source --save registrations.jsonl
python3 p2p_server.py --public-host cameras.example.com -v
python3 selftest.py  # 先停掉伺服器再跑
```

## 管理與監控

Docker 版的管理面板在 `http://localhost:9090/`（直接執行時預設為 `8081`，可用 `--admin-port` 修改）：

- `/`：自動更新的面板，顯示攝影機、手機連線與國家（GeoLite2）
- `/api/connections`：完整的 JSON 快照
- `/api/health`：健康檢查
- `/metrics`：Prometheus 格式，可接 Grafana
- `/api/stats`：公開計數（CORS `*`），顯示過去 1 小時 / 24 小時 / 7 天 / 30 天有多少攝影機與手機使用這台伺服器

加上 `--admin-token ...` 後，除了 `/metrics` 與 `/api/stats`，其他頁面都需要 `Authorization: Bearer ...` 或 `?token=`。

## 安全性

這是大約 2010 年的協定，**所有帳密都是明文傳送**：

- 攝影機註冊時，管理密碼直接放在 HTTP GET 參數裡。
- query / hole 封包以純 ASCII 帶著 8 碼 ID 與密碼。
- 影像指令使用可還原的 Basic auth。

請自己架設伺服器，讓只有*你自己*的流量經過它，並盡量放在防火牆或區網後面。單行日誌會移除 `userpwd`；JSONL 檔刻意保留身分（裝置 ID）但不保留密碼。在任何安裝上啟用前，請先檢查程式碼，確保維持這個行為。

## 目前進度

- 已找回 2010 與 2012 年的 Android 用戶端，並整理出封包格式。
- 根據韌體字串與封包擷取重新實作協定。
- 中繼模式通過 `selftest.py`；實體 IBT-200CP 在 DNS 指向伺服器後，註冊請求會立即出現。
- 除了已擷取到的格式之外，攝影機端的註冊與 keepalive 格式仍未確認；未知封包會以十六進位記錄下來。

## 使用倫理

這是用來救回你自己擁有、但已被原廠放棄的硬體的工具。本專案刻意不實作攝影機接管功能；陌生人的攝影機註冊時，伺服器只會記錄它的裝置 ID，完整帳密不會寫進持久化日誌。請保持這個原則。

## 授權

[MIT](LICENSE)
