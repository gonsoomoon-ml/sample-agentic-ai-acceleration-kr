# Copyright 2026 © Amazon.com and Affiliates: This deliverable is considered Developed Content as defined in the AWS Service Terms.

"""집계 기간(월/일)의 **단일 진실원** — 리포팅 타임존(REPORTING_TIMEZONE, 기본 KST) 경계.

이 자산의 집계 데이터는 전부 **리포팅 타임존 경계**로 버킷된다
(기본값 Asia/Seoul = KST; 네 서비스 모두 같은 REPORTING_TIMEZONE env 를 읽는다):

  * ``usage.daily_aggregates.date``
      = ``DATE(requested_at AT TIME ZONE 'Asia/Seoul')``
      (cost-recorder-worker/src/worker/daily_aggregator.py:43)
  * 그 집계 cron 자체가 ``AsyncIOScheduler(timezone="Asia/Seoul")`` KST 00:10 이고
      **"어제 KST"** 를 집계한다 (worker/main.py:93, daily_aggregator.py:26)
  * admin-api 는 조회·집계 전부 KST 로 못박아 두었다
      (admin-api/src/app/core/usage_filters.py — DEVLOG §59)
  * admin-api scheduler 는 ROI 행을 ``current_kst_period()`` 로 **쓴다**
      (admin-api/src/app/scheduler/main.py:33)

gateway-proxy 만 ``datetime.now(tz=timezone.utc).strftime("%Y-%m")`` 로 UTC 월/일을
쓰고 있었다. pod TZ 가 UTC 라 ``datetime.now()`` 도 같은 문제다. 그 결과:

  1. **월 경계 9시간** — 매월 1일 KST 00:00~09:00 동안 UTC 는 아직 지난달이다.
     ``budget:*:{period}`` 카운터가 새 달로 넘어가지 않아, 지난달 예산을 다 쓴 팀이
     **새 달 첫 9시간 동안 계속 차단**된다. 반대로 월말 마지막 9시간은 이미 다음 달
     카운터로 새어 예산이 조기 리셋된다.
  2. **서비스 간 불일치** — ``budget.budget_usages.period`` 행을 gateway 는 UTC 월로
     **쓰고**(cost_stream → batch_flusher), admin-api 는 ``current_kst_period()`` 로
     **읽는다**(routers/budgets.py:194). 그 9시간 동안 admin 화면의 팀 예산 사용액이
     조회 대상 행 자체가 없어 $0 으로 보인다.
  3. **매일 9시간 구멍** — ``/v1/usage/me`` 는 "어제까지는 DB(KST 버킷) + 오늘은 Redis
     카운터" 로 합산하는데, 경계 두 개가 서로 9시간 어긋나 KST 하루의 00:00~09:00
     구간이 **양쪽 어디에도 안 들어간다**(아래 상세).

⚠️ **읽는 쪽만 고치면 안 된다.** admin-api scheduler 가 같은 교훈을 주석으로 남겼다:
   읽는 쪽만 KST 로 바꾸면 쓰는 쪽이 만든 UTC 키/행을 조회하지 못해 9시간짜리
   "지표가 0" 창이 생긴다. 그래서 gateway-proxy 의 period/date 파생 지점을 **전부**
   이 모듈로 모아 한꺼번에 KST 로 맞춘다.

전환 영향(운영) — 데이터 이관은 필요 없지만 **배포 시각**은 골라야 한다:

  * 월 카운터(``budget:*:{period}``, ``budget_usages.period``): UTC 월과 KST 월은
    **KST 1일 00:00~09:00** 에만 갈린다. 그 창을 피하면 기존 키·행과 문자열이 같아
    무영향이다. 그 창에 배포하면 예산 카운터가 새 달 행으로 점프한다(= 사용액이
    0 으로 보이고, 그만큼 예산이 조기 리셋된다).
  * 일 카운터(``usage:daily:*:{date}``): UTC 일자와 KST 일자는 **매일 00:00~09:00 KST**
    에 갈린다. 그 창에 배포하면 그 시점까지 옛 UTC 키에 쌓인 당일 부분합이 고아가
    되어 ``/v1/usage/me`` 의 "오늘" 숫자가 한 번 작아진다. 고아 키는 TTL 48h
    (batch_flusher.py:298) 로 자연 소멸하고, DB 집계(daily_aggregates)는 KST 로
    독립 집계되므로 **영구 손실은 없다**.

⇒ 권고: **KST 09:00~24:00 사이에 배포**하면 두 창을 모두 피한다(평시 배포 시간대와
   일치). 월 1일 오전은 특히 피할 것.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from app.config import get_settings

# KST = UTC+9 고정 오프셋 — 기본 리포팅 타임존(Asia/Seoul)의 오프셋이며, 테스트·
# 호환용 공개 이름이다(admin-api usage_filters.KST 와 같은 역할). 실제 경계 계산은
# 아래 _reporting_tz() 를 거친다 — REPORTING_TIMEZONE 을 Seoul 이 아닌 값으로 둔
# 배포에서는 KST 상수가 아니라 설정 타임존이 기준이다.
KST = timezone(timedelta(hours=9))


def _reporting_tz() -> ZoneInfo:
    """경계 계산용 tzinfo. 고정 오프셋이 아니라 zoneinfo 를 쓴다 — America/Los_Angeles
    처럼 DST 가 있는 리포팅 타임존에서는 월 경계의 UTC 오프셋이 3월/11월에 달라져
    고정 오프셋으로는 경계가 1시간 틀어진다(admin-api usage_filters._reporting_tz
    와 동일 규약 — 같은 budget_usages.period 행을 읽고 쓰므로 정의가 같아야 한다)."""
    return ZoneInfo(get_settings().reporting_timezone)


def current_kst_period() -> str:
    """"지금"이 속한 리포팅 타임존 월을 ``YYYY-MM`` 으로.

    ``budget:*`` Redis 카운터 키와 ``budget_usages.period`` 의 기준 월이다.
    admin-api ``usage_filters.current_kst_period()`` 와 **같은 값**을 돌려줘야 한다 —
    두 서비스가 같은 행/키를 읽고 쓰고, 둘 다 REPORTING_TIMEZONE(기본 Asia/Seoul)을
    따른다.
    """
    now_local = datetime.now(_reporting_tz())
    return f"{now_local.year}-{now_local.month:02d}"


def current_kst_date() -> str:
    """"지금"이 속한 리포팅 타임존 날짜를 ``YYYY-MM-DD`` 으로.

    ``usage:daily:*`` Redis 카운터 키의 날짜이며, ``daily_aggregates.date`` 의 버킷
    기준과 같아야 한다. 왜 같아야 하는지:

    ``/v1/usage/me`` 는 한 달 사용량을 **두 소스에서** 합산한다 —
    ``date < 오늘`` 인 DB 집계 행 + 오늘분 Redis 카운터. DB 행은 리포팅 타임존 일자로
    버킷되어 있으므로, 오늘을 UTC 로 잡으면 리포팅 타임존의 새벽 몇 시간 동안
    "오늘"이 어제가 되어 그 날의 앞부분이 DB/Redis 양쪽 어디에도 안 들어간다.

    즉 매일 몇 시간 분량이 조용히 사라진다. 두 경계를 리포팅 타임존으로 통일해야
    이어붙는다.
    """
    return datetime.now(_reporting_tz()).strftime("%Y-%m-%d")
