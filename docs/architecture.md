# コード構成と変更時の確認

| ファイル | 責務 |
| --- | --- |
| `election/urls.py` | 公開URLと `election` 名前空間の入り口 |
| `election/auth_urls.py` / `auth_views.py` | ログイン・パスワード管理 |
| `election/management_urls.py` / `management_views.py` | 管理委員向けの画面、HTTP入力と権限確認 |
| `election/views.py` | 投票者向けの画面、投票セッションと確定処理 |
| `election/admin.py` | Django管理サイト |
| `election/forms.py` | 入力フォームとCSVファイルの検証 |
| `election/permissions.py` | 担当年度のアクセス制御 |
| `election/services/voting.py` | 電子・書面投票で共有する人数制限、候補者状態、表示順 |
| `election/services/tokens.py` | 投票URLの発行・再送・検証に共通のハッシュ処理 |
| `election/services/result_export.py` | 結果CSVの公開条件、列・行・ファイル名の生成 |
| `election/responses.py` | CSVダウンロードのHTTPヘッダーとBOM付きレスポンス |
| `election/services/` のその他のモジュール | 年度設定、名簿取込、候補者・有権者生成、書面票、開票、抽選 |
| `election/templates/election/includes/account_nav.html` | 認証画面・管理画面に共通のユーザーメニュー |

フォームとサービスからビューをインポートせず、共有する業務ルールはサービスへ置きます。
管理画面とDjango管理サイトの結果CSVは、同じサービスとレスポンス生成処理を使います。
URLを分割しても、既存のパスと `election:` のURL名は維持します。

電子投票の最終確定では、トランザクション内で有権者をロックし、投票済み状態・
受付期間・候補者の有効性を再確認します。書面票の登録数チェックも選挙のロック内で行います。
これらの境界を変更する場合は、単なる表示の変更と分けて検討してください。

## 検証

```bash
.venv/bin/python manage.py test --noinput
.venv/bin/python manage.py check
.venv/bin/python manage.py makemigrations --check --dry-run
```

`election/tests.py` は管理・権限・開票などの既存テスト、`election/test_auth.py` は認証、
`election/test_voting.py` は電子投票の受付から確定までの回帰テストです。
開発設定はSQLiteを使うため、DBロックの同時実行特性は本番と同じPostgreSQL環境で別途確認します。
