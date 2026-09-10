# PASJ Election System

日本加速器学会（PASJ）の会長選挙・代議員選挙を実施するための Django ベースの選挙システムです。

本システムでは、Excelから出力した会員名簿CSVをPostgreSQLへ取り込み、有権者・候補者を生成し、メールで配布した会員固有の投票 URL から投票を行います。

投票者情報と投票内容はデータベース上で分離し、

* 誰が投票済みか
* 誰が誰に投票したか

を分離して管理することで、秘密投票を実現します。

通常の選挙状況確認、メール送信状況確認、投票率確認、開票結果確認、抽選結果確認は Django Admin GUI から行えます。

CUI の Django management command は、初期設定、データ生成、メール送信、開票、抽選、障害対応、監査用途として残します。

本番環境の初回構築・更新手順と設定ひな型は
[`deploy/README.md`](deploy/README.md) を参照してください。

GitHub Releaseは、Semantic Versioning形式のタグをpushすると
`.github/workflows/release.yml` により自動作成されます。

```bash
git tag -a v1.0.0 -m "Release v1.0.0"
git push origin v1.0.0
```

`v1.0.0-rc.1` のようにハイフンを含むタグはプレリリースになります。
GitHub Actionsの画面から、既存タグを指定して手動実行することもできます。
ReleaseにはAnsibleが使用するソースアーカイブとSHA-256ファイルが添付されます。

---

# 1. 対応する選挙

## 会長選挙

### 予備選挙

* 選挙権: 正会員
* 正会員の中から会長候補者を推薦
* 10名以上から推薦された会員が本選挙候補者となる

### 本選挙

* 選挙権: 正会員
* 1人1票
* 最多得票者1名を会長当選者とする
* 最多得票者が同票の場合は、自動確定せず別途処理する

---

## 代議員選挙

### 予備選挙

* 選挙権: 正会員
* 1人最大10名まで推薦可能
* 企業枠・一般枠を合わせて最大10名
* 3名以上から推薦された会員が本選挙候補者となる

### 本選挙

* 選挙権: 正会員
* 1人最大10名まで投票可能
* 企業枠・一般枠を合わせて最大10名

定数:

* 一般枠: 25名
* 企業枠: 5名
* 合計: 30名

企業枠は名簿CSVの「所属」が

```text
企業関係
```

で判定します。

それ以外は一般枠として扱います。

定数境界で同票となった場合は、システム上で抽選を行います。

---

# 2. 技術構成

* AlmaLinux 10
* Python 3.12
* Django 6.0
* PostgreSQL
* SMTP
* Gunicorn
* nginx

開発時確認済み:

```text
Python 3.12.13
Django 6.0.8
```

---

# 3. ディレクトリ構成

```text
/opt/election/
├── .venv/
├── etc/
│   └── election.env
├── private/
├── results/
└── src/
    ├── manage.py
    ├── config/
    ├── election/
    │   ├── admin.py
    │   ├── models.py
    │   ├── views.py
    │   ├── urls.py
    │   ├── management/
    │   │   └── commands/
    │   ├── services/
    │   │   ├── counting.py
    │   │   └── lottery.py
    │   └── templates/
    ├── requirements.txt
    ├── election.env.example
    ├── .gitignore
    └── README.md
```

秘密情報は `/opt/election/etc/election.env` に保存し、Git では管理しません。

---

# 4. Python 仮想環境

仮想環境:

```bash
/opt/election/.venv
```

依存パッケージのインストール:

```bash
/opt/election/.venv/bin/pip install -r requirements.txt
```

依存関係の保存:

```bash
/opt/election/.venv/bin/pip freeze > requirements.txt
```

---

# 5. 環境変数

実設定ファイル:

```text
/opt/election/etc/election.env
```

テンプレート:

```text
election.env.example
```

例:

```bash
cp election.env.example /opt/election/etc/election.env
```

パーミッション:

```bash
chown election:election /opt/election/etc/election.env
chmod 600 /opt/election/etc/election.env
```

CUI実行前:

```bash
set -a
source /opt/election/etc/election.env
set +a
```

---

# 6. PostgreSQL

Migration:

```bash
cd /opt/election/src

sudo -E -u election \
  /opt/election/.venv/bin/python manage.py migrate
```

Django確認:

```bash
sudo -E -u election \
  /opt/election/.venv/bin/python manage.py check
```

---

# 7. 会員名簿CSV取り込み

Excel名簿をCSV UTF-8形式で保存し、次の列を取り込みます。

| CSV列 | Django | 用途 |
| --- | --- | --- |
| `会員番号` | `member_no` | 会員番号 |
| `会員名` | `last_name`, `first_name` | 氏名 |
| `会員種別` | `employee_type` | 選挙権判定 |
| `所属` | `business_category` | 企業枠判定 |
| `所属所属機関名` | `affiliation` | 所属機関 |
| `ＭＬ用メールアドレス` | `email` | 投票メール送信先 |

正会員判定:

```text
会員種別.startswith("正会員")
```

企業枠判定:

```text
所属 == "企業関係"
```

Dry-run:

```bash
sudo -E -u election \
  /opt/election/.venv/bin/python manage.py \
  import_members_csv \
  --cycle 2027 \
  --file /安全な保存先/修正_20260805名簿.csv \
  --dry-run
```

実行:

```bash
sudo -E -u election \
  /opt/election/.venv/bin/python manage.py \
  import_members_csv \
  --cycle 2027 \
  --file /安全な保存先/修正_20260805名簿.csv
```

---

# 8. 候補者生成

## 代議員予備選挙

```bash
sudo -E -u election \
  /opt/election/.venv/bin/python manage.py \
  generate_representative_candidates \
  --cycle 2027
```

## 会長予備選挙

```bash
sudo -E -u election \
  /opt/election/.venv/bin/python manage.py \
  generate_president_candidates \
  --cycle 2027
```

---

# 9. 有権者生成

例: 代議員予備選挙

```bash
sudo -E -u election \
  /opt/election/.venv/bin/python manage.py \
  generate_voters \
  --cycle 2027 \
  --office representative \
  --phase preliminary
```

会長:

```text
--office president
```

本選挙:

```text
--phase final
```

---

# 10. 投票URL

投票URL:

```text
https://vote.pasj.jp/v/<token>/
```

token は暗号学的乱数で生成します。

DBには生tokenではなく、

```text
SHA-256(token)
```

のみ保存します。

token認証後は Django session へ移行し、URLからtokenを除去します。

---

# 11. テスト用token CSV

テスト時のみ、投票URLをCSVに書き出せます。

```bash
sudo -E -u election \
  /opt/election/.venv/bin/python manage.py \
  generate_tokens \
  --cycle 2027 \
  --office representative \
  --phase preliminary \
  --base-url https://vote.pasj.jp/v/ \
  --output /opt/election/private/test.csv
```

CSVには有効なtokenが含まれるため厳重に管理します。

本番ではCSVを使用しません。

---

# 12. 投票メール

## テストメール

```bash
sudo -E -u election \
  /opt/election/.venv/bin/python manage.py \
  send_test_voting_email \
  --cycle 2027 \
  --office representative \
  --phase preliminary \
  --to test@example.jp \
  --base-url https://vote.pasj.jp/v/
```

このテストメールでは実際の有権者tokenを使用しません。

---

## 本番メール

```bash
sudo -E -u election \
  /opt/election/.venv/bin/python manage.py \
  send_voting_emails \
  --cycle 2027 \
  --office representative \
  --phase preliminary \
  --base-url https://vote.pasj.jp/v/
```

本番送信では、

```text
token生成
↓
メール本文生成
↓
SMTP送信
↓
DBにはtoken_hashのみ保存
```

とし、生tokenはファイルへ保存しません。

---

# 13. メール再送

```bash
sudo -E -u election \
  /opt/election/.venv/bin/python manage.py \
  resend_voting_email \
  --cycle 2027 \
  --office representative \
  --phase preliminary \
  --member m151114 \
  --base-url https://vote.pasj.jp/v/
```

再送時は新しいtokenを発行します。

旧URLは無効になります。

投票済み会員には再発行できません。

---

# 14. メール送信トラブル確認

```bash
sudo -E -u election \
  /opt/election/.venv/bin/python manage.py \
  list_email_issues \
  --cycle 2027 \
  --office representative \
  --phase preliminary
```

---

# 15. 投票処理

投票処理:

```text
メール固有URL
      ↓
token認証
      ↓
session作成
      ↓
tokenをURLから除去
      ↓
候補者ランダム表示
      ↓
候補者選択
      ↓
確認画面
      ↓
投票確定
      ↓
SELECT FOR UPDATE
      ↓
匿名Ballot保存
      ↓
voted_at更新
      ↓
transaction commit
      ↓
session破棄
```

---

# 16. 候補者表示順

候補者は50音順や会員番号順ではなく、投票者ごとにランダム表示します。

同じ投票者が再読み込みした場合は同じ順序になるよう、決定論的shuffleを利用します。

---

# 17. 秘密投票

有権者情報:

```text
VoterParticipation
```

投票内容:

```text
Ballot
BallotChoice
```

両者には直接のForeignKeyを持たせません。

```text
VoterParticipation
    ×
Ballot
```

そのため、

```text
誰が投票済みか
```

は確認できますが、

```text
誰が誰に投票したか
```

を直接特定できない構造です。

---

# 18. 二重投票防止

投票確定時に、

```text
SELECT FOR UPDATE
```

を利用します。

同じtokenから同時にPOSTされても、最初の1票だけが成立します。

---

# 19. 選挙状況確認 CUI

```bash
sudo -E -u election \
  /opt/election/.venv/bin/python manage.py \
  election_status \
  --cycle 2027 \
  --office representative \
  --phase preliminary
```

確認項目:

* 有権者数
* token発行数
* メール送信数
* 投票済み人数
* 未投票人数
* 投票率
* Ballot数
* voted_at と Ballot数の整合性

---

# 20. Django Admin

Django Adminを日常の選挙管理GUIとして使用します。

主な管理対象:

```text
ElectionCycle
Election
MemberSnapshot
Candidate
VoterParticipation
LotteryDraw
```

個別の `Ballot` / `BallotChoice` は通常のAdminには公開しません。

---

# 21. Election一覧

Election一覧では以下を表示します。

* 選挙年度
* 会長 / 代議員
* 予備選挙 / 本選挙
* ステータス
* 開始日時
* 終了日時
* 有権者数
* メール送信数
* 投票済み人数
* 投票率
* Ballot数
* 整合性

例:

```text
2027 | 代議員 | 本選挙 | 投票終了
-------------------------------------
有権者      933
メール      933 / 933
投票済      721 / 933
投票率      77.28 %
Ballot      721
整合性      OK
```

---

# 22. Election詳細画面

詳細画面では選挙状況サマリーを表示します。

```text
選挙状況

有権者        933
メール送信済  933
メール未送信    0
投票済        721
未投票        212
投票率      77.28 %
Ballot        721
整合性         OK
```

---

# 23. Candidate Admin

候補者一覧では以下を表示します。

* 選挙
* 会員
* 一般枠 / 企業枠
* 得票数
* status

status例:

```text
ELIGIBLE
QUALIFIED
ACCEPTED
DECLINED
DISQUALIFIED
ELECTED
LOTTERY
NOT_ELECTED
```

フィルターで、

* 当選
* 抽選対象
* 落選

などを絞り込めます。

---

# 24. VoterParticipation Admin

確認項目:

* 会員
* 選挙
* token発行済み / 未発行
* メール送信済み / 未送信
* 投票済み / 未投票
* メール送信試行回数

以下は読み取り専用とします。

```text
token_hash
token_issued_at
email_sent_at
email_send_attempts
voted_at
created_at
```

---

# 25. 代議員予備選挙 開票

CUI:

```bash
sudo -E -u election \
  /opt/election/.venv/bin/python manage.py \
  count_representative_preliminary \
  --cycle 2027 \
  --dry-run
```

確定:

```bash
sudo -E -u election \
  /opt/election/.venv/bin/python manage.py \
  count_representative_preliminary \
  --cycle 2027
```

3票以上で本選挙進出です。

---

# 26. 会長予備選挙 開票

```bash
sudo -E -u election \
  /opt/election/.venv/bin/python manage.py \
  count_president_preliminary \
  --cycle 2027
```

10票以上で本選挙進出です。

---

# 27. 代議員本選挙 GUI開票

代議員本選挙は Django Admin から開票できます。

条件:

```text
office = representative
phase  = final
status = closed
```

Election詳細画面に、

```text
[開票する]
```

ボタンが表示されます。

---

# 28. 開票プレビュー

`開票する` を押すと、DBを変更せずに開票結果を計算します。

表示例:

```text
開票プレビュー

Ballot数: 721

一般枠
------------------------
定数           25
確定当選       24
境界得票      143
残議席          1
抽選対象        3

企業枠
------------------------
定数            5
確定当選        5
抽選なし
```

確定当選者と抽選対象者の、

* 会員番号
* 氏名
* 所属
* 得票数

も表示します。

---

# 29. GUI開票確定

プレビュー画面で、

```text
[この内容で開票を確定]
```

を押すと結果をDBへ保存します。

候補者status:

```text
確定当選
→ ELECTED

境界同票
→ LOTTERY

落選
→ NOT_ELECTED
```

開票処理はtransaction内で実行します。

Election行も `select_for_update()` でロックします。

---

# 30. 開票ロジック共通化

GUI専用の開票ロジックは持たせません。

共通ロジック:

```text
election/services/counting.py
```

構成:

```text
Management Command
        ↓
services/counting.py
        ↑
Django Admin
```

これによりCUIとGUIで同じ集計ロジックを使用します。

---

# 31. 代議員本選挙 CUI開票

GUIを使わずCUIでも実行できます。

Dry-run:

```bash
sudo -E -u election \
  /opt/election/.venv/bin/python manage.py \
  count_representative_final \
  --cycle 2027 \
  --dry-run
```

確定:

```bash
sudo -E -u election \
  /opt/election/.venv/bin/python manage.py \
  count_representative_final \
  --cycle 2027
```

---

# 32. 境界同票

例えば一般枠25議席で、

```text
24位  150票
25位  143票
26位  143票
27位  143票
```

の場合、

```text
確定当選     24名
残議席        1
抽選対象      3名
```

となります。

開票確定時に、

```text
LotteryDraw
LotteryCandidate
```

を自動作成します。

---

# 33. GUI抽選

`LotteryDraw` Admin詳細画面で、未実行の抽選には、

```text
[抽選を実行]
```

ボタンが表示されます。

---

# 34. 抽選プレビュー

抽選実行前に以下を表示します。

```text
抽選プレビュー

選挙:
2027年度 代議員本選挙

枠:
一般枠

境界得票:
143票

残議席:
1

抽選対象:
3名
```

対象者一覧:

```text
会員番号
氏名
所属
得票
```

この段階ではDB変更を行いません。

---

# 35. 抽選実行

プレビュー画面で、

```text
[この条件で抽選を実行]
```

を押すと抽選を実施します。

抽選seed:

```python
secrets.token_hex(32)
```

候補者ごとのscore:

```text
SHA256(
    domain
    + election_id
    + category
    + vote_count
    + member_no
    + seed
)
```

score昇順で必要人数を当選とします。

---

# 36. 抽選監査情報

抽選後は以下を保存します。

`LotteryDraw`:

```text
seed
algorithm
result_hash
executed_at
```

`LotteryCandidate`:

```text
candidate
score
selected
```

抽選は一度しか実行できません。

---

# 37. 抽選ロジック共通化

共通ロジック:

```text
election/services/lottery.py
```

構成:

```text
run_lottery management command
           ↓
services/lottery.py
           ↑
Django Admin
```

CUIとGUIで同一ロジックを使用します。

---

# 38. GUI開票後の結果サマリー

Election詳細画面に開票結果を直接表示します。

代議員本選挙:

```text
開票結果サマリー

一般枠 25 / 25
企業枠  5 / 5

代議員30名が確定しています。
```

一般枠・企業枠の当選者について、

* 会員番号
* 氏名
* 所属
* 得票数

を表示します。

---

# 39. 未実行抽選

抽選が残っている場合:

```text
未実行の抽選が1件あります。
```

とElection詳細画面に警告を表示します。

---

# 40. 会長本選挙結果

開票確定後:

```text
会長当選者

山田 太郎
315票
KEK
```

のようにElection詳細画面へ表示します。

現時点ではGUI開票機能は代議員本選挙を対象としています。

---

# 41. 最終結果 CUI

## 代議員

```bash
sudo -E -u election \
  /opt/election/.venv/bin/python manage.py \
  show_representative_results \
  --cycle 2027
```

CSV:

```bash
sudo -E -u election \
  /opt/election/.venv/bin/python manage.py \
  show_representative_results \
  --cycle 2027 \
  --output /opt/election/results/representative-2027.csv
```

## 会長

```bash
sudo -E -u election \
  /opt/election/.venv/bin/python manage.py \
  show_president_results \
  --cycle 2027
```

---

# 42. Django AdminとCUIの役割

通常運用:

```text
Django Admin
├── 選挙状況確認
├── メール送信状況確認
├── 投票率確認
├── 候補者確認
├── 得票数確認
├── 開票プレビュー
├── 開票確定
├── 抽選プレビュー
├── 抽選実行
└── 最終結果確認
```

CUI:

```text
Management Commands
├── 会員名簿CSV取り込み
├── 候補者生成
├── 有権者生成
├── token生成
├── メール送信
├── メール再送
├── 状態検査
├── 開票
├── 抽選
└── CSV出力
```

CUIは、

* 初期設定
* バッチ処理
* 障害対応
* 復旧
* 監査

用途として残します。

---

# 43. Git

初期化:

```bash
cd /opt/election/src

git init
git branch -M main
```

確認:

```bash
git status
```

追加:

```bash
git add .
```

再確認:

```bash
git status
```

秘密情報が含まれていないことを確認してから、

```bash
git commit -m "Initial PASJ election system"
```

---

# 44. Git管理しないもの

以下はコミットしません。

```text
/opt/election/etc/election.env
/opt/election/.venv/
private/
results/
*.csv
```

特に以下の秘密情報をGitへ入れないでください。

* Django SECRET_KEY
* PostgreSQL password
* SMTP password
* 生token
* 有効な投票URL
* token CSV

---

# 45. 本番構成

予定:

```text
Internet
   |
   v
vote.pasj.jp:443
   |
   v
nginx
   |
   v
Gunicorn
   |
   v
Django
   |
   +-- PostgreSQL
   |
   +-- SMTP
```

---

# 46. 本番設定

本番化時に設定する項目:

```text
DEBUG = False
ALLOWED_HOSTS
CSRF_TRUSTED_ORIGINS
SESSION_COOKIE_SECURE
CSRF_COOKIE_SECURE
SECURE_SSL_REDIRECT
SECRET_KEY environment variable
```

加えて、

* Gunicorn
* systemd
* nginx
* TLS証明書
* PostgreSQL backup
* Django static files
* Admin access制限
* access log対策

を実施します。

---

# 47. token URLとログ

投票URL:

```text
/v/<token>/
```

には秘密tokenが含まれます。

nginxの通常access logへtokenをそのまま記録しないよう、本番環境ではログ設定を調整します。

token認証後は303 redirectし、通常の投票画面ではtokenをURLに残しません。

---

# 48. バックアップ

最低限、以下のタイミングでPostgreSQLバックアップを取得します。

* 選挙開始直前
* 投票終了直後
* 開票直前
* 抽選直前
* 最終結果確定後

設定ファイル:

```text
/opt/election/etc/
```

もバックアップ対象とします。

---

# 49. 現在の実装状況

実装済み:

```text
会員名簿CSV取り込み
MemberSnapshot
候補者生成
有権者生成
token生成
投票URL
メール送信
メール再送
匿名投票
二重投票防止
候補者ランダム表示
予備選挙集計
本選挙集計
境界同票判定
抽選
結果CSV
選挙status CUI
Django Admin
Admin選挙状況表示
Admin候補者得票表示
Admin開票結果サマリー
代議員本選挙 GUI開票
GUI開票プレビュー
GUI開票確定
GUI抽選プレビュー
GUI抽選実行
```

---

# 50. 今後の作業

主な残作業:

```text
TLS
SMTP本番設定
管理者アクセス制限
ログ設定
tokenログ対策
バックアップ
実データでの通しテスト
```

必要に応じて、

```text
会長予備選挙 GUI開票
会長本選挙 GUI開票
メール送信 GUI
会員名簿取り込み GUI
選挙作成ウィザード
```

なども追加できます。

---

# 51. 基本運用イメージ

```text
会員名簿CSV取り込み
   ↓
ElectionCycle作成
   ↓
Election作成
   ↓
候補者生成
   ↓
有権者生成
   ↓
token発行・メール送信
   ↓
投票開始
   ↓
Django Adminで投票状況監視
   ↓
投票終了
   ↓
GUI開票プレビュー
   ↓
GUI開票確定
   ↓
境界同票あり？
   ├─ No → 結果確定
   └─ Yes
        ↓
     GUI抽選プレビュー
        ↓
     GUI抽選実行
        ↓
     結果確定
   ↓
最終結果確認
   ↓
CSV保存
   ↓
DBバックアップ
```

このシステムでは、通常の選挙管理業務を Django Admin から行いながら、CUI management command を保守・監査・復旧用として併用する構成とします。
