/**
 * Janus Companion App - Event Feed Types (v1)
 * Defined per app/INTEGRATION.md contract.
 */

export type EventType =
  | 'session_started'
  | 'user_speaking'
  | 'heard'
  | 'turn_closed'
  | 'agent_said'
  | 'agent_interrupted'
  | 'call_planned'
  | 'call_held'
  | 'confirmation_asked'
  | 'call_cancelled'
  | 'call_sent'
  | 'call_result'
  | 'device_state'
  | 'goal_finished'
  | 'ledger_summary';

// 'unknown' until the agent's session_started says which backend it uses.
export type BackendType = 'real' | 'mock' | 'unknown';

export type AgentSaidKind = 'filler' | 'ack' | 'clarify' | 'inform' | 'final';

export type CallKind = 'read' | 'write';

export type ReasonCode =
  | 'user_speaking'
  | 'settling'
  | 'needs_go_ahead'
  | 'needs_confirmation'
  | 'duplicate'
  | 'outcome_unknown'
  | 'other';

export type CallOutcome =
  | 'confirmed'
  | 'already'
  | 'unconfirmed'
  | 'refused'
  | 'failed';

export interface BaseEvent {
  v: 1;
  seq: number;
  t_ms: number;
  type: EventType | string;
}

export interface SessionStartedEvent extends BaseEvent {
  type: 'session_started';
  packs: string[];
  backend: BackendType;
}

export interface UserSpeakingEvent extends BaseEvent {
  type: 'user_speaking';
  active: boolean;
}

export interface HeardEvent extends BaseEvent {
  type: 'heard';
  turn_id: string;
  text: string;
}

export interface TurnClosedEvent extends BaseEvent {
  type: 'turn_closed';
  turn_id: string;
}

export interface AgentSaidEvent extends BaseEvent {
  type: 'agent_said';
  text: string;
  kind: AgentSaidKind;
}

export interface AgentInterruptedEvent extends BaseEvent {
  type: 'agent_interrupted';
}

export interface CallPlannedEvent extends BaseEvent {
  type: 'call_planned';
  call_id: string;
  tool: string;
  label: string;
  kind: CallKind;
  args: Record<string, any>;
  security: boolean;
}

export interface CallHeldEvent extends BaseEvent {
  type: 'call_held';
  call_id: string;
  reason_code: ReasonCode | string;
  reason: string;
}

export interface ConfirmationAskedEvent extends BaseEvent {
  type: 'confirmation_asked';
  call_id: string;
  question: string;
}

export interface CallCancelledEvent extends BaseEvent {
  type: 'call_cancelled';
  call_id: string;
  reason: string;
}

export interface CallSentEvent extends BaseEvent {
  type: 'call_sent';
  call_id: string;
  args: Record<string, any>;
}

export interface CallResultEvent extends BaseEvent {
  type: 'call_result';
  call_id: string;
  outcome: CallOutcome;
  summary: string;
}

export interface DeviceStateEvent extends BaseEvent {
  type: 'device_state';
  device: string;
  label: string;
  capability: string;
  attribute: string;
  value: any;
  backend: BackendType;
}

export interface GoalFinishedEvent extends BaseEvent {
  type: 'goal_finished';
  goal_id: string;
  completed: boolean;
  summary: string;
}

export interface LedgerSummaryEvent extends BaseEvent {
  type: 'ledger_summary';
  committed: number;
  withdrawn_before_sending: number;
  unconfirmed: number;
  failed: number;
  duplicates_blocked: number;
}

export type JanusEvent =
  | SessionStartedEvent
  | UserSpeakingEvent
  | HeardEvent
  | TurnClosedEvent
  | AgentSaidEvent
  | AgentInterruptedEvent
  | CallPlannedEvent
  | CallHeldEvent
  | ConfirmationAskedEvent
  | CallCancelledEvent
  | CallSentEvent
  | CallResultEvent
  | DeviceStateEvent
  | GoalFinishedEvent
  | LedgerSummaryEvent;

// State structures for the UI
export type ActionCardStatus =
  | 'planned'
  | 'held'
  | 'waiting_for_yes'
  | 'sent'
  | 'confirmed'
  | 'already'
  | 'unconfirmed'
  | 'cancelled'
  | 'refused'
  | 'failed';

export interface ActionCardData {
  callId: string;
  tool: string;
  label: string;
  kind: CallKind;
  security: boolean;
  args: Record<string, any>;
  status: ActionCardStatus;
  statusText: string;
  heldReason?: string;
  cancelledReason?: string;
  confirmationQuestion?: string;
  resultSummary?: string;
  outcome?: CallOutcome;
  isFinal: boolean;
  timestamp: number;
}

export interface CaptionEntry {
  id: string;
  sender: 'user' | 'agent';
  turnId?: string;
  text: string;
  kind?: AgentSaidKind;
  timestamp: number;
  isInterrupted?: boolean;
}

export interface DeviceStateData {
  device: string;
  label: string;
  capability: string;
  attribute: string;
  value: any;
  backend: BackendType;
  updatedAt: number;
}

export interface LedgerData {
  committed: number;
  withdrawnBeforeSending: number;
  unconfirmed: number;
  failed: number;
  duplicatesBlocked: number;
}
