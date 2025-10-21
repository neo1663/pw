# Bluesky Outreach Automation Tool

このリポジトリは、Bluesky (AT Protocol) 上で以下の作業を自動化する Python 製のツールを提供します。

- 指定したターゲットアカウントのフォロワーを順番にフォロー
- フォローしたユーザーの最新ポストにいいね
- 自身のアカウントに新しくフォローしてきたユーザーへ DM を送信
- 複数アカウント・プロキシ設定に対応

> ⚠️ **注意**: Bluesky の API は開発中であり、特に DM 関連のエンドポイントは仕様が変更される場合があります。公式クライアントで DM が利用可能であることを確認した上で、自己責任でご利用ください。

## 必要条件

- Python 3.10 以上
- Bluesky のアプリパスワード (通常のパスワードではなく、設定画面から発行するアプリ専用パスワード)
- `requests` と `PyYAML` ライブラリ (リポジトリ同梱の `requirements.txt` を参照)

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## 使い方

1. `config.example.yaml` をコピーして、自分の環境向けに調整します。

   ```bash
   cp config.example.yaml config.yaml
   ```

2. 作成した `config.yaml` に、アカウント情報・ターゲット・DM 文面などを入力します。

3. Dry-run で設定を検証します (API にはアクセスしません)。

   ```bash
   python -m bluesky_tool.automator --config config.yaml --dry-run
   ```

4. 問題がなければ実行します。

   ```bash
   python -m bluesky_tool.automator --config config.yaml
   ```

## 設定ファイル

`config.yaml` は YAML 形式で、複数アカウントを定義できます。主な項目は以下の通りです。

- `storage.directory`: フォロー済み／DM 済みの情報を保存するディレクトリ
- `accounts`: 処理対象のアカウント一覧
  - `handle`: ログインに使用するハンドル
  - `app_password`: アプリパスワード
  - `service`: 利用する AT Protocol サーバー (通常は `https://bsky.social`)
  - `proxy`: `http://user:pass@host:port` の形式で指定すると、そのアカウントの通信にプロキシを使用
  - `follow_delay_seconds` / `like_delay_seconds`: 各アクション後に挟む待機秒数 (0 を指定すると待機なし)
  - `follow_targets`: フォロー対象アカウントの一覧
    - `handle`: ターゲットアカウントのハンドル
    - `follow_limit`: 1 回の実行でフォローする人数上限
    - `like_latest_post`: フォロー直後に最新ポストへいいねするか
    - `like_limit`: 1 回の実行でいいねする人数上限
  - `dm`: 新規フォロワーへの DM 設定
    - `enabled`: DM を送る場合は `true`
    - `message`: 送信する文面。`{handle}` `{displayName}` `{did}` のプレースホルダーが使用できます。
    - `limit_per_run`: 1 回の実行で送信する DM の上限
    - `cooldown_hours`: 同一ユーザーに再送するまでの待機時間 (0 で再送なし)

## 状態管理

各アカウントごとの状態は `storage.directory` で指定したフォルダに JSON 形式で保存され、再実行時には前回のフォロー・DM 情報を参照します。ファイルは自動生成されます。

## ロギング

`--log-level` オプションでログレベルを変更できます。

```bash
python -m bluesky_tool.automator --config config.yaml --log-level DEBUG
```

## 注意事項

- 本ツールは Bluesky の利用規約に従った範囲で使用してください。
- API レート制限を避けるため、十分な待機時間を設定することを推奨します。
- DM エンドポイントが利用できない環境では、自動的に処理を中断します。

## 免責

本ツールの使用により生じた損害について、作者は責任を負いません。自己責任でご利用ください。
