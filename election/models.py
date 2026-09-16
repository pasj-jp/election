import uuid

from django.db import models

class ElectionCycle(models.Model):
    """
    1年度分の選挙全体。
    例: 2027年度 加速器学会選挙
    """

    year = models.PositiveIntegerField(unique=True)
    name = models.CharField(max_length=200)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "選挙年度"
        verbose_name_plural = "選挙年度"
        ordering = ["-year"]

    def __str__(self):
        return self.name


class Election(models.Model):
    """
    予備選挙・本選挙それぞれを表す。
    """

    class Office(models.TextChoices):
        PRESIDENT = "president", "会長"
        REPRESENTATIVE = "representative", "代議員"

    class Phase(models.TextChoices):
        PRELIMINARY = "preliminary", "予備選挙"
        FINAL = "final", "本選挙"

    class Status(models.TextChoices):
        DRAFT = "draft", "準備中"
        OPEN = "open", "投票受付中"
        CLOSED = "closed", "投票終了"
        COUNTED = "counted", "開票済"

    cycle = models.ForeignKey(
        ElectionCycle,
        on_delete=models.PROTECT,
        related_name="elections",
    )

    office = models.CharField(
        max_length=20,
        choices=Office.choices,
    )

    phase = models.CharField(
        max_length=20,
        choices=Phase.choices,
    )

    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.DRAFT,
    )

    start_at = models.DateTimeField()
    end_at = models.DateTimeField()

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "選挙"
        verbose_name_plural = "選挙"
        constraints = [
            models.UniqueConstraint(
                fields=["cycle", "office", "phase"],
                name="unique_election_per_cycle_office_phase",
            ),
        ]
        ordering = ["cycle", "phase", "office"]

    def __str__(self):
        return (
            f"{self.cycle.year}年度 "
            f"{self.get_office_display()} "
            f"{self.get_phase_display()}"
        )


class MemberSnapshot(models.Model):
    """
    選挙開始時点の会員情報のスナップショット。

    元の会員名簿をそのまま参照し続けるのではなく、
    選挙時点の情報を固定するためのテーブル。
    """

    class RepresentativeCategory(models.TextChoices):
        GENERAL = "general", "一般枠"
        CORPORATE = "corporate", "企業枠"

    cycle = models.ForeignKey(
        ElectionCycle,
        on_delete=models.PROTECT,
        related_name="members",
    )

    member_no = models.CharField(
        max_length=32,
    )

    last_name = models.CharField(
        max_length=100,
    )

    first_name = models.CharField(
        max_length=100,
    )

    email = models.EmailField()

    affiliation = models.CharField(
        max_length=255,
        blank=True,
    )

    employee_type = models.CharField(
        max_length=100,
    )

    business_category = models.CharField(
        max_length=100,
        blank=True,
    )

    representative_category = models.CharField(
        max_length=20,
        choices=RepresentativeCategory.choices,
    )

    is_eligible_voter = models.BooleanField(
        default=False,
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "会員リスト"
        verbose_name_plural = "会員リスト"
        constraints = [
            models.UniqueConstraint(
                fields=["cycle", "member_no"],
                name="unique_member_per_cycle",
            ),
        ]
        ordering = ["member_no"]

    def __str__(self):
        return f"{self.member_no} {self.last_name} {self.first_name}"


class Candidate(models.Model):
    """
    各選挙の候補者。

    MemberSnapshotを参照するので、元の名簿変更の影響を受けない。
    """

    class Status(models.TextChoices):
        ELIGIBLE = "eligible", "被選挙人"
        QUALIFIED = "qualified", "本選挙進出"
        ACCEPTED = "accepted", "立候補承諾"
        DECLINED = "declined", "辞退"
        DISQUALIFIED = "disqualified", "資格なし"

        ELECTED = "elected", "当選"
        LOTTERY = "lottery", "抽選対象"
        NOT_ELECTED = "not_elected", "落選"

    election = models.ForeignKey(
        Election,
        on_delete=models.PROTECT,
        related_name="candidates",
    )

    member = models.ForeignKey(
        MemberSnapshot,
        on_delete=models.PROTECT,
        related_name="candidacies",
    )

    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.ELIGIBLE,
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "候補者"
        verbose_name_plural = "候補者"
        constraints = [
            models.UniqueConstraint(
                fields=["election", "member"],
                name="unique_candidate_per_election",
            ),
        ]

    def __str__(self):
        return (
            f"{self.election}: "
            f"{self.member.last_name} {self.member.first_name}"
        )

class VoterParticipation(models.Model):
    """
    ある選挙について、ある会員が投票資格を持ち、
    投票済みかどうかを管理する。

    投票内容（Ballot）とは意図的に関連付けない。
    """

    election = models.ForeignKey(
        Election,
        on_delete=models.PROTECT,
        related_name="voter_participations",
    )

    member = models.ForeignKey(
        MemberSnapshot,
        on_delete=models.PROTECT,
        related_name="voter_participations",
    )

    # 後でメール固有URL用トークンのハッシュを保存する
    token_hash = models.CharField(
        max_length=64,
        null=True,
        blank=True,
        unique=True,
    )

    voted_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
    )
    
    token_issued_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    email_sent_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    email_send_attempts = models.PositiveIntegerField(
        default=0,
    )

    class Meta:
        verbose_name = "有権者"
        verbose_name_plural = "有権者"
        constraints = [
            models.UniqueConstraint(
                fields=["election", "member"],
                name="unique_voter_participation",
            ),
        ]

    def __str__(self):
        status = "投票済" if self.voted_at else "未投票"

        return (
            f"{self.election} "
            f"{self.member.member_no} "
            f"{status}"
        )


class Ballot(models.Model):
    """
    匿名投票そのもの。

    VoterParticipation / MemberSnapshot へのForeignKeyを
    あえて持たせない。

    これにより、
    「誰が投票したか」と「誰に投票したか」を分離する。
    """

    ballot_uuid = models.UUIDField(
        default=uuid.uuid4,
        unique=True,
        editable=False,
    )

    election = models.ForeignKey(
        Election,
        on_delete=models.PROTECT,
        related_name="ballots",
    )

    submitted_at = models.DateTimeField(
        auto_now_add=True,
    )

    def __str__(self):
        return (
            f"{self.election} "
            f"{self.ballot_uuid}"
        )


class BallotChoice(models.Model):
    """
    1枚のBallotで選択された候補者。

    代議員選挙なら1 Ballotにつき最大10件。
    会長本選挙なら最大1件。
    """

    ballot = models.ForeignKey(
        Ballot,
        on_delete=models.CASCADE,
        related_name="choices",
    )

    candidate = models.ForeignKey(
        Candidate,
        on_delete=models.PROTECT,
        related_name="ballot_choices",
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["ballot", "candidate"],
                name="unique_candidate_per_ballot",
            ),
        ]

    def __str__(self):
        return (
            f"{self.ballot.ballot_uuid}: "
            f"{self.candidate.member.last_name} "
            f"{self.candidate.member.first_name}"
        )

class LotteryDraw(models.Model):
    """
    定数境界同票の抽選。

    categoryごとに1回だけ作成する。
    """

    class Algorithm(models.TextChoices):
        SHA256_V1 = "sha256-v1", "SHA-256 v1"

    election = models.ForeignKey(
        Election,
        on_delete=models.PROTECT,
        related_name="lottery_draws",
    )

    category = models.CharField(
        max_length=20,
        choices=MemberSnapshot.RepresentativeCategory.choices,
    )

    vote_count = models.PositiveIntegerField()

    seats_remaining = models.PositiveIntegerField()

    seed = models.CharField(
        max_length=128,
        blank=True,
    )

    algorithm = models.CharField(
        max_length=32,
        choices=Algorithm.choices,
        default=Algorithm.SHA256_V1,
    )

    executed_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    result_hash = models.CharField(
        max_length=64,
        blank=True,
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
    )

    class Meta:
        verbose_name = "抽選"
        verbose_name_plural = "抽選"
        constraints = [
            models.UniqueConstraint(
                fields=[
                    "election",
                    "category",
                ],
                name="unique_lottery_per_election_category",
            ),
        ]

    def __str__(self):
        return (
            f"{self.election} "
            f"{self.get_category_display()} 抽選"
        )


class LotteryCandidate(models.Model):
    """
    抽選対象候補者。
    """

    lottery = models.ForeignKey(
        LotteryDraw,
        on_delete=models.CASCADE,
        related_name="candidates",
    )

    candidate = models.ForeignKey(
        Candidate,
        on_delete=models.PROTECT,
        related_name="lottery_entries",
    )

    selected = models.BooleanField(
        default=False,
    )

    score = models.CharField(
        max_length=64,
        blank=True,
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=[
                    "lottery",
                    "candidate",
                ],
                name="unique_candidate_per_lottery",
            ),
        ]

    def __str__(self):
        return (
            f"{self.lottery}: "
            f"{self.candidate.member}"
        )
