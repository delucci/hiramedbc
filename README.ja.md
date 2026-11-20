# HirameDBC

[English](README.md) | **日本語**

> この文書は英語版 [README.md](README.md) の翻訳です。内容に差異がある場合は英語版を優先してください。

Docker の公式 MySQL イメージを使い捨てコンテナとして起動し、複数バージョンの MySQL クライアント（`mysql` / `mysqldump`）を使い分ける CLI です。macOS と Windows に対応しています。

- MySQL クライアントをローカルにインストールしません
- 接続先と MySQL バージョンを自由に組み合わせられます（`hirame c 5.7 legacy` / `hirame c 8.4 production`）
- パスワードの保存先は、接続のたびに入力・OS のキーチェーン（macOS キーチェーン / Windows 資格情報マネージャー）・設定ファイルへの平文保存から選べます
- 認証情報はコマンドライン引数や環境変数に載せず、コンテナ内のオプションファイルとして渡します

> インストール・更新・削除は **パッケージ名 `hiramedbc`**、普段の操作は **コマンド名 `hirame`** です。

## 必要なもの

| 必要なもの | 入れ方 |
|---|---|
| [uv](https://docs.astral.sh/uv/) | macOS: `brew install uv` / Windows: `winget install astral-sh.uv` |
| Docker 互換ランタイム（`docker` コマンドが使えること） | macOS: Docker Desktop / OrbStack / Colima / Rancher Desktop<br>Windows: Docker Desktop / Rancher Desktop（WSL2 が必要） |
| 接続先 MySQL へのネットワーク接続 | - |

Python と git を別途インストールする必要はありません（uv が Python を用意します）。Docker Desktop は組織規模によって有償サブスクリプションが必要です。導入前に最新の利用条件を確認してください。

## インストール

```bash
uv tool install hiramedbc            # インストール
uv tool install hiramedbc==0.1.1     # バージョンを固定する場合
uv tool upgrade hiramedbc            # 更新
uv tool uninstall hiramedbc          # 削除
uvx --from hiramedbc hirame          # インストールせずに一時実行
```

インストール直後に `hirame` が見つからない場合は、`uv tool update-shell` を実行してからターミナルを開き直してください。

## はじめかた

```bash
hirame init           # 環境チェック → 設定ファイル生成 → 接続先登録 → イメージ取得
hirame c production   # 接続
```

`init` は次の順に進みます。Docker が使えない状態でも、警告を出したうえで設定まで進みます。

1. 動作環境チェック（`hirame doctor` と同じ内容）
2. 設定ファイルの生成（デフォルトで使う MySQL のバージョンを尋ねます）
3. 接続先の登録（続けて複数登録できます）
4. 登録した接続先で使うイメージを今取得するかの確認

## コマンド

| コマンド | 略語 | 説明 |
|---|---|---|
| `hirame init` | - | 初期セットアップ |
| `hirame connect [VERSION] ALIAS [DATABASE]` | `c` | DB に接続する |
| `hirame add [ALIAS]` | `a` | 接続先を登録する（対話形式） |
| `hirame list` | `ls` | 登録済みの接続先を表示する |
| `hirame edit ALIAS` | `e` | 接続先を編集する（対話形式。Enter で現在値のまま） |
| `hirame remove ALIAS [-y]` | `rm` | 接続先と、キーチェーンのパスワードを削除する（`-y` で確認を省略） |
| `hirame dump [VERSION] ALIAS DATABASE OUTPUT [-f] [-- 追加オプション・テーブル名]` | `d` | `mysqldump` でダンプを取得する（`-f` で上書き確認を省略） |
| `hirame config path` | - | 使用中の設定ファイルのパスを表示する |
| `hirame config edit` | - | 設定ファイルをエディタで開く |
| `hirame doctor [--pull [VERSION...]] [--clean]` | `dr` | 動作環境チェック・イメージの事前取得・停止済みで残ったコンテナの削除 |
| `hirame version` | `v` | バージョンを表示する |

`connect` は省略できます。`hirame production` / `hirame 8.4 production` は `hirame connect ...` と同じ意味です。全コマンドで `--config <PATH>` を指定できます。

### 使用例

```bash
hirame c production               # 設定のバージョンで接続
hirame c 8.4 production my_db     # バージョンとデータベースを指定
hirame c production < script.sql  # SQL ファイルを流し込む（ユーザー名は設定で固定しておく）

hirame d production my_db backup.sql                         # ダンプ
hirame d production my_db - > backup.sql                     # 標準出力へ
hirame d production my_db backup.sql -- --no-data users      # mysqldump にオプション・テーブル名を渡す

hirame doctor --pull              # 設定で使う全バージョンのイメージを取得
hirame doctor --pull 5.7 8.4      # 指定したバージョンを取得
```

### バージョンの決まり方

1. コマンド引数の `VERSION`
2. 接続先ごとの `version`
3. 設定ファイルの `defaults.version`

接続時に、使うバージョンとその由来（引数 / 接続先設定 / デフォルト）を表示します。第 1 引数が `8` / `8.4` / `8.0.36` のようなバージョン形式ならバージョン、そうでなければ接続先名として扱います。そのため、バージョン形式の名前やコマンド名（`list` / `ls` など）は接続先名に使えません。

## 認証情報の扱い

ユーザー名は「固定（保存する）」か「都度入力」を選べます。パスワードは「都度入力」「キーチェーンに保存」「設定ファイルに平文で保存」から選べます。

| ユーザー名 | パスワード | 想定ケース | 接続時の入力 |
|---|---|---|---|
| 固定 | 固定（設定ファイル） | パスワードを頻繁に書き換えるローカル開発 | なし |
| 固定 | 固定（キーチェーン） | 開発用の共有アカウント | なし |
| 固定 | 都度入力 | パスワードを保存したくない・定期的に変わる | パスワードのみ |
| 都度入力 | 都度入力 | 個人アカウント・払い出された一時アカウント | 両方 |

- 本番の接続先は、パスワードを保存せず都度入力にする運用も選べます。登録時のパスワード保存の既定は「いいえ」です。
- 保存済みの値を一時的に使わない場合は、`--ask-user`（パスワードも入力になります）/ `--ask-password` を付けます。
- 設定ファイルに保存したパスワードは平文です。自分だけが読める端末でのみ使ってください（macOS では 0600 権限で作成され、他のユーザーが読める場合は `hirame doctor` が警告します）。
- 設定ファイルに保存したパスワードを変更するには、`hirame config edit` で直接編集します（`hirame edit` でも再入力できます）。
- `list` はパスワードの値を表示せず、保存先（キーチェーン / 設定ファイル）だけを示します。
- macOS では、初回のキーチェーンアクセス時に許可ダイアログが表示されます。「常に許可」を選んでください。uv 経由で Python が更新されると、再度表示されることがあります。

## 設定ファイル

| 優先度 | 指定方法 |
|---|---|
| 1 | `--config <PATH>` |
| 2 | 環境変数 `HIRAMEDBC_CONFIG` |
| 3 | `~/.hiramedbc/config.json`（Windows: `%USERPROFILE%\.hiramedbc\config.json`） |

```json
{
  "connections": {
    "development": { "host": "dev-db.example.com", "port": 3306, "user": "app_user", "password": "keyring", "version": "8.0" },
    "production": {
      "host": "prod-db.example.com", "port": 3306, "user": "", "password": "", "version": "8.4",
      "ssl": { "mode": "VERIFY_IDENTITY", "ca": "/path/to/ca.pem" }
    }
  },
  "defaults": { "version": "8.4" }
}
```

| 項目 | 内容 |
|---|---|
| `host` | 必須。接続先ホスト |
| `port` | 省略時 `3306` |
| `user` | 値あり＝固定。空＝都度入力 |
| `password` | 空＝都度入力。`"keyring"`＝キーチェーンから取得。それ以外の値はそのままパスワード（平文）として使います。そのため、パスワードそのものが `keyring` の場合は設定ファイルに保存できません |
| `version` | 接続先ごとの MySQL バージョン（イメージタグ） |
| `ssl` | 任意。`mode`（`DISABLED` / `PREFERRED` / `REQUIRED` / `VERIFY_CA` / `VERIFY_IDENTITY`）と `ca` / `cert` / `key`（ホスト上のパス）。証明書は読み取り専用でコンテナにマウントされます |
| `defaults.version` | 全接続先共通のデフォルトバージョン |

ユーザー名を都度入力にしてパスワードだけ固定する組み合わせは使えません（パスワードは接続先単位で保存するため）。設定ファイルは macOS では所有者のみ読み書きできる権限（0600）で作られます。

## 注意事項

- **ホストマシン上の MySQL に接続する場合**: コンテナ内の `localhost` / `127.0.0.1` はコンテナ自身を指します。`host.docker.internal` を指定してください。
- **Apple Silicon**: 公式イメージに arm64 版が無いバージョン（5.7 など）は、amd64 のエミュレーションで実行します。ランタイムの Rosetta / QEMU が無効だと動きません（`hirame doctor` で確認できます）。
- **コンテナの後片付け**: 接続終了・エラー・Ctrl+C のいずれでもコンテナを削除します。端末を強制的に閉じた場合などに残ったコンテナは `hirame doctor` が検出します。停止済みのものは `hirame doctor --clean` で削除できます。実行中のまま残ったものは別のターミナルで接続中の可能性があるため自動では削除せず、名前を表示します。接続中でなければ `docker rm -f <名前>` で削除してください。
- **ダンプファイル**: ダンプは所有者のみ読み書きできる権限で保存されます。失敗・中断した場合、既存のファイルは変更されません。`--` の後の引数は `mysqldump ... <データベース> <追加引数>` の順で渡します（テーブル名も指定できます）。`--` なしの余分な引数はエラーになります。
- **パスワードに使えない文字**: ダブルクォート（`"`）とシングルクォート（`'`）の両方を含むパスワード、NUL 文字を含むパスワードは扱えません（MySQL のオプションファイルで安全に表現できないため）。

## 開発

```bash
uv sync                                         # 依存の導入
HIRAMEDBC_CONFIG=./config/config.json uv run hirame ls   # リポジトリ内の設定で動かす（config/config.json は gitignore 済み）
uv run pytest                                   # テスト
uv run ruff check . && uv run ruff format --check .
```

- リリース: `pyproject.toml` の `version` を上げて main にマージし、Actions タブから「Publish to PyPI」を手動実行します。Trusted Publishing で PyPI に公開され（API トークンはリポジトリに保存しません）、その後 `v<version>` のタグと GitHub Release が自動で作成されます。

## ライセンス

[MIT](LICENSE)
