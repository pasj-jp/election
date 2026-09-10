# 本番デプロイ手順

AlmaLinux、`/opt/election`、Nginx、Gunicorn、systemdを前提とします。
ドメイン、証明書パス、ユーザー名は実環境に合わせて変更してください。

## 1. 初回準備

```bash
sudo useradd --system --home-dir /opt/election --shell /sbin/nologin election
sudo mkdir -p /opt/election/{etc,src}
sudo python3.12 -m venv /opt/election/.venv
sudo chown -R election:election /opt/election
```

このリポジトリを `/opt/election/src` に配置します。秘密情報はリポジトリ外へ置きます。

```bash
sudo cp election.env.example /opt/election/etc/election.env
sudo chown election:election /opt/election/etc/election.env
sudo chmod 600 /opt/election/etc/election.env
sudoedit /opt/election/etc/election.env
```

`ELECTION_SECRET_KEY` は次のような方法で生成できます。

```bash
/opt/election/.venv/bin/python -c 'import secrets; print(secrets.token_urlsafe(64))'
```

## 2. systemdとNginx

ひな型をコピーし、ドメイン、証明書パス、OS上のNginxグループ名を確認します。

```bash
sudo cp deploy/systemd/election.service /etc/systemd/system/election.service
sudo cp deploy/nginx/election.conf /etc/nginx/conf.d/election.conf
sudo systemctl daemon-reload
sudo nginx -t
sudo systemctl enable election.service nginx.service
```

AlmaLinuxでSELinuxを有効にしている場合は、NginxによるUnixソケット接続と
静的ファイルの読み取りに必要なラベル・ポリシーを環境に合わせて設定します。
SELinux自体を無効化して回避しないでください。

## 3. 初回デプロイ

先にPostgreSQLのデータベースとユーザーを作成し、Nginx用TLS証明書を配置します。
その後、リポジトリのルートから実行します。

```bash
sudo ./scripts/deploy.sh
sudo systemctl start nginx.service
```

確認します。

```bash
sudo systemctl status election.service
sudo journalctl -u election.service -n 100 --no-pager
curl -I https://vote.pasj.jp/admin/login/
```

## 4. 通常の更新

1. PostgreSQLのバックアップを取得する
2. `/opt/election/src` のコードを対象リビジョンへ切り替える
3. デプロイスクリプトを実行する
4. 管理画面と投票画面を確認する

```bash
sudo -u postgres pg_dump --format=custom election \
  > /安全な保存先/election-$(date +%Y%m%d-%H%M%S).dump

cd /opt/election/src
sudo ./scripts/deploy.sh
```

スクリプトは依存関係の更新、設定検査、マイグレーション、静的ファイル収集、
Gunicorn再起動を順番に行います。コードの取得は意図しないブランチ更新を避けるため、
スクリプトには含めていません。

## 5. 切り戻し

アプリケーションだけを切り戻す場合は、直前のリビジョンへコードを戻して
`scripts/deploy.sh` を再実行します。ただし、適用済みマイグレーションとの互換性を
事前に確認してください。DBを戻す必要がある場合はサービスを停止し、運用手順に従って
バックアップから復元します。選挙データを含むため、安易に逆マイグレーションしません。

## 6. Nginxログ上の注意

`/v/<token>/` は秘密情報を含むため、ひな型ではNginxの該当アクセスログを無効化し、
Gunicornのアクセスログも有効にしていません。アクセスログを追加する場合は、token URLを
確実にマスクしてください。
