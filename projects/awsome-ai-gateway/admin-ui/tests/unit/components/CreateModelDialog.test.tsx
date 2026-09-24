// Copyright 2026 © Amazon.com and Affiliates: This deliverable is considered Developed Content as defined in the AWS Service Terms.

/**
 * 모델 추가 다이얼로그 — 검증 실패 원인이 화면에 반드시 보여야 한다.
 *
 * 배경(실제 고객 장애): 스키마가 폼에 없는 키(max_tokens/context_window)를 required 로 요구해
 * 등록이 항상 실패했는데, 그 키에는 대응 <input> 이 없어 fieldErrors 가 렌더되지 못했다.
 * 사용자는 원인 없는 "Validation failed" 만 보게 되어 자력 진단이 불가능했다.
 *
 * 여기서는 서버 액션을 대역으로 바꿔 "폼에 없는 키의 필드 에러" 를 반환시키고,
 * 그 키 이름이 화면 텍스트에 노출되는지 확인한다.
 */

import { readFileSync } from 'node:fs';
import path from 'node:path';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { CreateModelDialog } from '@/components/models/CreateModelDialog';
import type { ModelListItem } from '@/types/entities';

const createModelAction = vi.fn();
const updateModelAction = vi.fn();

vi.mock('@/lib/actions/models', () => ({
  createModelAction: (...a: unknown[]) => createModelAction(...a),
  updateModelAction: (...a: unknown[]) => updateModelAction(...a),
  // 와이어 이름 힌트 — 목록 자체를 검증하는 테스트는 없으니 빈 목록으로 고정.
  listWireNamesAction: vi.fn(async () => ({ success: true as const, data: [] })),
}));

// next-intl 은 메시지 provider 없이는 throw 한다 — 키를 그대로 돌려주는 대역으로 대체.
vi.mock('next-intl', () => ({
  useTranslations: () => (key: string) => key,
}));

const toast = vi.fn();
vi.mock('@/components/common/ToastProvider', () => ({
  useToast: () => ({ toast }),
}));

/** 필수 입력을 채워 submit 이 브라우저 required 검증에 막히지 않게 한다. */
function fillRequiredFields() {
  fireEvent.change(screen.getByLabelText(/Alias/), {
    target: { name: 'alias', value: 'codex-gpt-5.6-sol' },
  });
  fireEvent.change(screen.getByLabelText(/Provider/), {
    target: { name: 'provider', value: 'BEDROCK_MANTLE_OPENAI' },
  });
  fireEvent.change(screen.getByLabelText(/Model ID/), {
    target: { name: 'model_id', value: 'openai.gpt-5.6-sol' },
  });
  fireEvent.change(screen.getByLabelText('priceInput'), {
    target: { name: 'input_price_per_1k', value: '0.011' },
  });
  fireEvent.change(screen.getByLabelText('priceOutput'), {
    target: { name: 'output_price_per_1k', value: '0.0495' },
  });
}

describe('CreateModelDialog — 검증 실패 관측성', () => {
  beforeEach(() => {
    createModelAction.mockReset();
    updateModelAction.mockReset();
    toast.mockReset();
  });

  it('폼에 입력란이 없는 키의 필드 에러도 화면에 노출한다 (회귀: 원인 없는 Validation failed)', async () => {
    createModelAction.mockResolvedValue({
      success: false,
      error: 'Validation failed',
      // 폼에 대응 <input> 이 없는 키 — 과거에는 조용히 버려졌다.
      fieldErrors: { max_tokens: 'Required', context_window: 'Required' },
    });

    render(<CreateModelDialog isOpen onClose={() => {}} />);
    fillRequiredFields();
    fireEvent.submit(screen.getByRole('button', { name: 'register' }).closest('form')!);

    await waitFor(() => {
      const alerts = screen.getAllByRole('alert').map((n) => n.textContent ?? '');
      const joined = alerts.join(' | ');
      expect(joined).toContain('Validation failed');
      // 핵심: 원인 키가 화면에 보여야 한다.
      expect(joined).toContain('max_tokens');
      expect(joined).toContain('context_window');
    });
  });

  it('폼에 입력란이 있는 키는 해당 필드 아래에만 표시하고 상단 메시지를 오염시키지 않는다', async () => {
    createModelAction.mockResolvedValue({
      success: false,
      error: 'Validation failed',
      fieldErrors: { alias: 'Alias is required' },
    });

    render(<CreateModelDialog isOpen onClose={() => {}} />);
    fillRequiredFields();
    fireEvent.submit(screen.getByRole('button', { name: 'register' }).closest('form')!);

    await waitFor(() => {
      const joined = screen.getAllByRole('alert').map((n) => n.textContent ?? '').join(' | ');
      expect(joined).toContain('Alias is required');
      // alias 는 대응 input 이 있으므로 상단 요약에 키 이름을 덧붙이지 않는다.
      expect(joined).not.toContain('alias (');
    });
  });

  it('성공하면 토스트를 띄우고 닫는다', async () => {
    createModelAction.mockResolvedValue({ success: true, data: undefined });
    const onClose = vi.fn();

    render(<CreateModelDialog isOpen onClose={onClose} />);
    fillRequiredFields();
    fireEvent.submit(screen.getByRole('button', { name: 'register' }).closest('form')!);

    await waitFor(() => {
      expect(toast).toHaveBeenCalledWith(
        expect.objectContaining({ type: 'success' })
      );
      expect(onClose).toHaveBeenCalled();
    });
  });

  it('전송 payload 에 폼에 없는 필드를 넣지 않는다', async () => {
    createModelAction.mockResolvedValue({ success: true, data: undefined });

    render(<CreateModelDialog isOpen onClose={() => {}} />);
    fillRequiredFields();
    fireEvent.submit(screen.getByRole('button', { name: 'register' }).closest('form')!);

    await waitFor(() => expect(createModelAction).toHaveBeenCalled());
    const payload = createModelAction.mock.calls[0][0] as Record<string, unknown>;
    expect(payload).not.toHaveProperty('max_tokens');
    expect(payload).not.toHaveProperty('context_window');
    expect(payload.alias).toBe('codex-gpt-5.6-sol');
    expect(payload.provider).toBe('BEDROCK_MANTLE_OPENAI');
  });
});

/**
 * 편집 모드에서 Provider 는 alias 와 똑같이 읽기 전용이어야 한다.
 *
 * 배경(실제 결함): PUT /admin/models/{alias} 의 ModelUpdateRequest 에는 provider/api_format
 * 이 없고 update 서비스·리포지토리에도 변경 경로가 없다 — admin API 로는 바꿀 수 없는 불변
 * 필드다. 그런데 이 다이얼로그의 Provider <select> 에만 `disabled={isEditMode}` 가 빠져
 * 있어서(같은 파일 alias input 에는 있다) 운영자가 값을 바꿔 저장할 수 있었고,
 * updateModelAction 은 provider 를 아예 보내지 않으므로(lib/actions/models.ts:95~)
 * 초록색 성공 토스트만 뜨고 DB 는 그대로였다. 화면과 DB 가 갈라지는 조용한 실패다.
 * (api_format 은 이 폼에 컨트롤 자체가 없고 create 시 provider 에서 파생되므로 잠글 대상이
 *  없다 — 그래서 안내 문구가 provider 와 api_format 을 함께 설명한다.)
 */
describe('CreateModelDialog — provider 는 편집 불가 (조용한 유실 회귀)', () => {
  const editModel: ModelListItem = {
    alias: 'cowork-opus',
    provider: 'BEDROCK_MANTLE',
    model_id: 'anthropic.claude-opus-4-1',
    endpoint_url: null,
    is_active: true,
    input_price_per_1k: 0.015,
    output_price_per_1k: 0.075,
    cache_creation_5m_price_per_1k: 0.01875,
    cache_creation_1h_price_per_1k: 0.03,
    cache_read_price_per_1k: 0.0015,
    max_tokens: 0,
    context_window: 0,
    description: 'Cowork Opus',
    display_name: 'Cowork Opus',
  };

  beforeEach(() => {
    createModelAction.mockReset();
    updateModelAction.mockReset();
    toast.mockReset();
  });

  // ── VACUITY CONTROL ────────────────────────────────────────────────────────
  // editModel 을 넘겼는데 실제로 편집 모드로 렌더되지 않으면 아래 disabled 어서션은
  // "우연히" 통과/실패할 수 있다. 그래서 provider 와 무관한 신호 두 개로 편집 모드를
  // 먼저 증명한다: alias input 이 잠기고(원래부터 있던 동작) 제출 버튼이 edit 이다.
  it('[vacuity] editModel 을 주면 정말 편집 모드로 렌더된다', () => {
    render(<CreateModelDialog isOpen onClose={() => {}} editModel={editModel} />);

    expect(screen.getByLabelText(/Alias/)).toBeDisabled();
    expect(screen.getByRole('button', { name: 'edit' })).toBeInTheDocument();
    expect(screen.getByText('aliasReadonly')).toBeInTheDocument();
    // 값 프리필까지 확인 — 이게 없으면 "잠겼지만 빈 값" 상태를 못 잡는다.
    expect(screen.getByLabelText(/Provider/)).toHaveValue('BEDROCK_MANTLE');
  });

  it('편집 모드에서는 Provider 를 바꿀 수 없고 읽기 전용 안내가 보인다', () => {
    render(<CreateModelDialog isOpen onClose={() => {}} editModel={editModel} />);

    expect(screen.getByLabelText(/Provider/)).toBeDisabled();
    expect(screen.getByText('providerReadonly')).toBeInTheDocument();
  });

  it('생성 모드에서는 Provider 를 바꿀 수 있다 (과잉 차단 아님)', () => {
    render(<CreateModelDialog isOpen onClose={() => {}} />);

    const select = screen.getByLabelText(/Provider/);
    expect(select).toBeEnabled();
    expect(screen.queryByText('providerReadonly')).not.toBeInTheDocument();
    // 실제로 선택이 반영되는지까지 — 잠금이 create 로 새지 않았음을 동작으로 확인한다.
    fireEvent.change(select, { target: { name: 'provider', value: 'OPENMODEL' } });
    expect(select).toHaveValue('OPENMODEL');
  });

  it('편집 저장 payload 의 provider 는 기존 값 그대로다 (잠금이 값을 비우지 않는다)', async () => {
    updateModelAction.mockResolvedValue({ success: true, data: undefined });

    render(<CreateModelDialog isOpen onClose={() => {}} editModel={editModel} />);
    fireEvent.submit(screen.getByRole('button', { name: 'edit' }).closest('form')!);

    await waitFor(() => expect(updateModelAction).toHaveBeenCalled());
    const [alias, payload] = updateModelAction.mock.calls[0] as [string, Record<string, unknown>];
    expect(alias).toBe('cowork-opus');
    // ⚠️ disabled 셀렉트가 값을 잃으면 ModelCreateSchema 의 provider min(1) 이 깨져
    //    편집이 100% 실패한다(zod 는 create/update 양쪽에 같은 스키마를 쓴다).
    expect(payload.provider).toBe('BEDROCK_MANTLE');
  });

  it('providerReadonly 안내 문구가 ko/en 양쪽에 있다 (키가 그대로 노출되지 않게)', () => {
    // next-intl 을 키 그대로 돌려주는 대역으로 바꿨기 때문에, 렌더 테스트만으로는
    // 메시지 누락(화면에 "providerReadonly" 가 그대로 보이는 상태)을 못 잡는다.
    for (const locale of ['ko', 'en']) {
      const messages = JSON.parse(
        readFileSync(path.join(process.cwd(), 'messages', `${locale}.json`), 'utf8')
      );
      expect(typeof messages.models.providerReadonly).toBe('string');
      expect(messages.models.providerReadonly.length).toBeGreaterThan(0);
    }
  });
});
