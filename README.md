# 處置股預判與回測

每個交易日晚上自動從 FinLab 下載資料、計算聽牌狀態與回測統計，並發布到 GitHub Pages。

## 第一次設定（約 5 分鐘）

1. **建立 repo**：GitHub 右上角 ＋ → New repository，名稱例如 `disposal-radar`，選 **Public**（免費帳號的 GitHub Pages 只能用公開 repo）。
2. **上傳檔案**：在 repo 頁面按 *uploading an existing file*，把這個資料夾裡的所有檔案拖進去，按 Commit。
   - 要確認 `.github/workflows/update.yml` 有上傳成功（Windows 有時不會帶上以「.」開頭的資料夾）。沒有的話：Add file → Create new file，檔名打 `.github/workflows/update.yml`，把內容貼上後 Commit。
3. **設定 API Key**：Settings → Secrets and variables → Actions → New repository secret
   - Name：`FINLAB_API_KEY`
   - Secret：你的 FinLab API Key
4. **開啟 Pages**：Settings → Pages → Build and deployment → Source 選 **GitHub Actions**。
5. **第一次執行**：Actions → 左側「更新處置股資料」→ Run workflow。約 5～10 分鐘後，網頁會在
   `https://你的帳號.github.io/disposal-radar/`

## 之後

- **自動更新**：週一到週五台灣時間 21:15 自動跑（GitHub 排程偶爾會延遲 10～30 分鐘）。
- **手動更新**：網頁標題旁的「更新資料」按鈕 → 開啟 Actions 頁面 → Run workflow。
- 公開 repo 若 60 天沒有任何 commit，GitHub 會暫停排程；在 Actions 頁面按一下 Enable 即可恢復。

## 檔案

| 檔案 | 用途 |
|---|---|
| `export_finlab.py` | 從 FinLab 下載處置、注意股、日 K、本益比、融資券，以及期交所股票期貨清單 |
| `process.py` | 計算聽牌狀態、處置事件、隔日觸發價，產生網頁 |
| `template.html` | 網頁樣板 |
| `.github/workflows/update.yml` | 排程與手動更新 |

## 本機執行

```
pip install -r requirements.txt
set FINLAB_API_KEY=你的key        （或在同資料夾放 finlab_key.txt）
python export_finlab.py
python process.py
```
# ToTheArena
