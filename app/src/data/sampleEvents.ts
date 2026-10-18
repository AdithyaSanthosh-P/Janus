// Auto-generated from app/sample_events.jsonl for offline demo file mode
import { JanusEvent } from '../types/janus';

export const SAMPLE_EVENTS: JanusEvent[] = [
  {
    "v": 1,
    "seq": 1,
    "t_ms": 0,
    "type": "session_started",
    "packs": [
      "smartthings",
      "device_care"
    ],
    "backend": "mock"
  },
  {
    "v": 1,
    "seq": 2,
    "t_ms": 50,
    "type": "device_state",
    "device": "dev-lock",
    "label": "Front door",
    "capability": "lock",
    "attribute": "lock",
    "value": "unlocked",
    "backend": "mock"
  },
  {
    "v": 1,
    "seq": 3,
    "t_ms": 50,
    "type": "device_state",
    "device": "dev-garage",
    "label": "Garage",
    "capability": "garageDoorControl",
    "attribute": "door",
    "value": "closed",
    "backend": "mock"
  },
  {
    "v": 1,
    "seq": 4,
    "t_ms": 50,
    "type": "device_state",
    "device": "dev-ac",
    "label": "AC",
    "capability": "thermostatCoolingSetpoint",
    "attribute": "coolingSetpoint",
    "value": 22,
    "backend": "mock"
  },
  {
    "v": 1,
    "seq": 5,
    "t_ms": 50,
    "type": "device_state",
    "device": "dev-light",
    "label": "Living room light",
    "capability": "switch",
    "attribute": "switch",
    "value": "on",
    "backend": "mock"
  },
  {
    "v": 1,
    "seq": 6,
    "t_ms": 50,
    "type": "device_state",
    "device": "dev-washer",
    "label": "Washer",
    "capability": "washerOperatingState",
    "attribute": "machineState",
    "value": "run",
    "backend": "mock"
  },
  {
    "v": 1,
    "seq": 7,
    "t_ms": 1200,
    "type": "user_speaking",
    "active": true
  },
  {
    "v": 1,
    "seq": 8,
    "t_ms": 3900,
    "type": "heard",
    "turn_id": "t-0001",
    "text": "I'm heading out. Lock the front door, set the AC to 24..."
  },
  {
    "v": 1,
    "seq": 9,
    "t_ms": 5200,
    "type": "heard",
    "turn_id": "t-0001",
    "text": "actually no, make it 26."
  },
  {
    "v": 1,
    "seq": 10,
    "t_ms": 5600,
    "type": "user_speaking",
    "active": false
  },
  {
    "v": 1,
    "seq": 11,
    "t_ms": 6100,
    "type": "turn_closed",
    "turn_id": "t-0001"
  },
  {
    "v": 1,
    "seq": 12,
    "t_ms": 6150,
    "type": "agent_said",
    "text": "Okay, one moment.",
    "kind": "filler"
  },
  {
    "v": 1,
    "seq": 13,
    "t_ms": 7400,
    "type": "call_planned",
    "call_id": "c-0001",
    "tool": "st_lock",
    "label": "Lock front door",
    "kind": "write",
    "args": {
      "device": "front door"
    },
    "security": false
  },
  {
    "v": 1,
    "seq": 14,
    "t_ms": 7400,
    "type": "call_planned",
    "call_id": "c-0002",
    "tool": "st_set_cooling_setpoint",
    "label": "Set AC to 24 \u00b0C",
    "kind": "write",
    "args": {
      "device": "AC",
      "celsius": 24
    },
    "security": false
  },
  {
    "v": 1,
    "seq": 15,
    "t_ms": 7410,
    "type": "call_held",
    "call_id": "c-0001",
    "reason_code": "settling",
    "reason": "Waiting a moment in case you change your mind"
  },
  {
    "v": 1,
    "seq": 16,
    "t_ms": 7410,
    "type": "call_held",
    "call_id": "c-0002",
    "reason_code": "settling",
    "reason": "Waiting a moment in case you change your mind"
  },
  {
    "v": 1,
    "seq": 17,
    "t_ms": 7420,
    "type": "call_cancelled",
    "call_id": "c-0002",
    "reason": "You changed it to 26"
  },
  {
    "v": 1,
    "seq": 18,
    "t_ms": 7420,
    "type": "call_planned",
    "call_id": "c-0003",
    "tool": "st_set_cooling_setpoint",
    "label": "Set AC to 26 \u00b0C",
    "kind": "write",
    "args": {
      "device": "AC",
      "celsius": 26
    },
    "security": false
  },
  {
    "v": 1,
    "seq": 19,
    "t_ms": 7430,
    "type": "agent_said",
    "text": "Locking the front door and setting the AC to 26.",
    "kind": "ack"
  },
  {
    "v": 1,
    "seq": 20,
    "t_ms": 8400,
    "type": "call_sent",
    "call_id": "c-0001",
    "args": {
      "device": "front door"
    }
  },
  {
    "v": 1,
    "seq": 21,
    "t_ms": 8400,
    "type": "call_sent",
    "call_id": "c-0003",
    "args": {
      "device": "AC",
      "celsius": 26
    }
  },
  {
    "v": 1,
    "seq": 22,
    "t_ms": 9300,
    "type": "device_state",
    "device": "dev-lock",
    "label": "Front door",
    "capability": "lock",
    "attribute": "lock",
    "value": "locked",
    "backend": "mock"
  },
  {
    "v": 1,
    "seq": 23,
    "t_ms": 9310,
    "type": "call_result",
    "call_id": "c-0001",
    "outcome": "confirmed",
    "summary": "Front door is locked"
  },
  {
    "v": 1,
    "seq": 24,
    "t_ms": 9800,
    "type": "device_state",
    "device": "dev-ac",
    "label": "AC",
    "capability": "thermostatCoolingSetpoint",
    "attribute": "coolingSetpoint",
    "value": 26,
    "backend": "mock"
  },
  {
    "v": 1,
    "seq": 25,
    "t_ms": 9810,
    "type": "call_result",
    "call_id": "c-0003",
    "outcome": "confirmed",
    "summary": "AC is set to 26 \u00b0C"
  },
  {
    "v": 1,
    "seq": 26,
    "t_ms": 10900,
    "type": "agent_said",
    "text": "Done. The front door is locked and the AC is set to 26.",
    "kind": "final"
  },
  {
    "v": 1,
    "seq": 27,
    "t_ms": 10950,
    "type": "goal_finished",
    "goal_id": "g-0001",
    "completed": true,
    "summary": "Front door locked; AC set to 26 \u00b0C"
  },
  {
    "v": 1,
    "seq": 28,
    "t_ms": 10960,
    "type": "ledger_summary",
    "committed": 2,
    "withdrawn_before_sending": 1,
    "unconfirmed": 0,
    "failed": 0,
    "duplicates_blocked": 0
  },
  {
    "v": 1,
    "seq": 29,
    "t_ms": 13000,
    "type": "user_speaking",
    "active": true
  },
  {
    "v": 1,
    "seq": 30,
    "t_ms": 14200,
    "type": "heard",
    "turn_id": "t-0002",
    "text": "And lock the front door."
  },
  {
    "v": 1,
    "seq": 31,
    "t_ms": 14500,
    "type": "user_speaking",
    "active": false
  },
  {
    "v": 1,
    "seq": 32,
    "t_ms": 15000,
    "type": "turn_closed",
    "turn_id": "t-0002"
  },
  {
    "v": 1,
    "seq": 33,
    "t_ms": 15050,
    "type": "agent_said",
    "text": "Okay, one moment.",
    "kind": "filler"
  },
  {
    "v": 1,
    "seq": 34,
    "t_ms": 16100,
    "type": "call_planned",
    "call_id": "c-0004",
    "tool": "st_lock",
    "label": "Lock front door",
    "kind": "write",
    "args": {
      "device": "front door"
    },
    "security": false
  },
  {
    "v": 1,
    "seq": 35,
    "t_ms": 16900,
    "type": "call_result",
    "call_id": "c-0004",
    "outcome": "already",
    "summary": "Front door was already locked; nothing sent"
  },
  {
    "v": 1,
    "seq": 36,
    "t_ms": 17600,
    "type": "agent_said",
    "text": "The front door is already locked, so I didn't send it again.",
    "kind": "final"
  },
  {
    "v": 1,
    "seq": 37,
    "t_ms": 17650,
    "type": "goal_finished",
    "goal_id": "g-0002",
    "completed": true,
    "summary": "Front door already locked"
  },
  {
    "v": 1,
    "seq": 38,
    "t_ms": 17660,
    "type": "ledger_summary",
    "committed": 2,
    "withdrawn_before_sending": 1,
    "unconfirmed": 0,
    "failed": 0,
    "duplicates_blocked": 1
  },
  {
    "v": 1,
    "seq": 39,
    "t_ms": 19500,
    "type": "user_speaking",
    "active": true
  },
  {
    "v": 1,
    "seq": 40,
    "t_ms": 20600,
    "type": "heard",
    "turn_id": "t-0003",
    "text": "Open the garage for me."
  },
  {
    "v": 1,
    "seq": 41,
    "t_ms": 20900,
    "type": "user_speaking",
    "active": false
  },
  {
    "v": 1,
    "seq": 42,
    "t_ms": 21400,
    "type": "turn_closed",
    "turn_id": "t-0003"
  },
  {
    "v": 1,
    "seq": 43,
    "t_ms": 21450,
    "type": "agent_said",
    "text": "Okay, one moment.",
    "kind": "filler"
  },
  {
    "v": 1,
    "seq": 44,
    "t_ms": 22500,
    "type": "call_planned",
    "call_id": "c-0005",
    "tool": "st_open_garage",
    "label": "Open garage",
    "kind": "write",
    "args": {
      "device": "garage"
    },
    "security": true
  },
  {
    "v": 1,
    "seq": 45,
    "t_ms": 22510,
    "type": "call_held",
    "call_id": "c-0005",
    "reason_code": "needs_confirmation",
    "reason": "Security action: waiting for your yes"
  },
  {
    "v": 1,
    "seq": 46,
    "t_ms": 22520,
    "type": "confirmation_asked",
    "call_id": "c-0005",
    "question": "That's a security action. Shall I go ahead and open the garage?"
  },
  {
    "v": 1,
    "seq": 47,
    "t_ms": 22530,
    "type": "agent_said",
    "text": "That's a security action. Shall I go ahead and open the garage?",
    "kind": "clarify"
  },
  {
    "v": 1,
    "seq": 48,
    "t_ms": 25000,
    "type": "user_speaking",
    "active": true
  },
  {
    "v": 1,
    "seq": 49,
    "t_ms": 26100,
    "type": "heard",
    "turn_id": "t-0004",
    "text": "Hmm, no, leave it."
  },
  {
    "v": 1,
    "seq": 50,
    "t_ms": 26300,
    "type": "user_speaking",
    "active": false
  },
  {
    "v": 1,
    "seq": 51,
    "t_ms": 26800,
    "type": "turn_closed",
    "turn_id": "t-0004"
  },
  {
    "v": 1,
    "seq": 52,
    "t_ms": 27900,
    "type": "call_cancelled",
    "call_id": "c-0005",
    "reason": "You said no"
  },
  {
    "v": 1,
    "seq": 53,
    "t_ms": 28000,
    "type": "agent_said",
    "text": "Okay, I didn't open the garage. Nothing was sent.",
    "kind": "final"
  },
  {
    "v": 1,
    "seq": 54,
    "t_ms": 28050,
    "type": "goal_finished",
    "goal_id": "g-0003",
    "completed": false,
    "summary": "Garage not opened; nothing sent"
  },
  {
    "v": 1,
    "seq": 55,
    "t_ms": 28060,
    "type": "ledger_summary",
    "committed": 2,
    "withdrawn_before_sending": 2,
    "unconfirmed": 0,
    "failed": 0,
    "duplicates_blocked": 1
  },
  {
    "v": 1,
    "seq": 56,
    "t_ms": 30500,
    "type": "user_speaking",
    "active": true
  },
  {
    "v": 1,
    "seq": 57,
    "t_ms": 31700,
    "type": "heard",
    "turn_id": "t-0005",
    "text": "Turn off the living room light."
  },
  {
    "v": 1,
    "seq": 58,
    "t_ms": 32000,
    "type": "user_speaking",
    "active": false
  },
  {
    "v": 1,
    "seq": 59,
    "t_ms": 32500,
    "type": "turn_closed",
    "turn_id": "t-0005"
  },
  {
    "v": 1,
    "seq": 60,
    "t_ms": 32550,
    "type": "agent_said",
    "text": "Okay, one moment.",
    "kind": "filler"
  },
  {
    "v": 1,
    "seq": 61,
    "t_ms": 33600,
    "type": "call_planned",
    "call_id": "c-0006",
    "tool": "st_switch",
    "label": "Turn off living room light",
    "kind": "write",
    "args": {
      "device": "living room light",
      "state": "off"
    },
    "security": false
  },
  {
    "v": 1,
    "seq": 62,
    "t_ms": 34600,
    "type": "call_sent",
    "call_id": "c-0006",
    "args": {
      "device": "living room light",
      "state": "off"
    }
  },
  {
    "v": 1,
    "seq": 63,
    "t_ms": 42100,
    "type": "call_result",
    "call_id": "c-0006",
    "outcome": "unconfirmed",
    "summary": "SmartThings accepted the command but the light did not report off"
  },
  {
    "v": 1,
    "seq": 64,
    "t_ms": 42800,
    "type": "agent_said",
    "text": "I sent the command, but SmartThings hasn't confirmed the light is off yet. I'm not going to call it done.",
    "kind": "final"
  },
  {
    "v": 1,
    "seq": 65,
    "t_ms": 42850,
    "type": "goal_finished",
    "goal_id": "g-0004",
    "completed": false,
    "summary": "Light command sent, not confirmed"
  },
  {
    "v": 1,
    "seq": 66,
    "t_ms": 42860,
    "type": "ledger_summary",
    "committed": 2,
    "withdrawn_before_sending": 2,
    "unconfirmed": 1,
    "failed": 0,
    "duplicates_blocked": 1
  },
  {
    "v": 1,
    "seq": 67,
    "t_ms": 45500,
    "type": "user_speaking",
    "active": true
  },
  {
    "v": 1,
    "seq": 68,
    "t_ms": 47800,
    "type": "heard",
    "turn_id": "t-0006",
    "text": "My washing machine stopped in the middle of a wash. What's wrong with it?"
  },
  {
    "v": 1,
    "seq": 69,
    "t_ms": 48100,
    "type": "user_speaking",
    "active": false
  },
  {
    "v": 1,
    "seq": 70,
    "t_ms": 48600,
    "type": "turn_closed",
    "turn_id": "t-0006"
  },
  {
    "v": 1,
    "seq": 71,
    "t_ms": 48650,
    "type": "agent_said",
    "text": "Okay, one moment.",
    "kind": "filler"
  },
  {
    "v": 1,
    "seq": 72,
    "t_ms": 49700,
    "type": "call_planned",
    "call_id": "c-0007",
    "tool": "get_device_status",
    "label": "Check washer status",
    "kind": "read",
    "args": {
      "device_type": "washer"
    },
    "security": false
  },
  {
    "v": 1,
    "seq": 73,
    "t_ms": 50300,
    "type": "device_state",
    "device": "dev-washer",
    "label": "Washer",
    "capability": "washerOperatingState",
    "attribute": "machineState",
    "value": "pause",
    "backend": "mock"
  },
  {
    "v": 1,
    "seq": 74,
    "t_ms": 50310,
    "type": "call_result",
    "call_id": "c-0007",
    "outcome": "confirmed",
    "summary": "Washer paused, error 4C"
  },
  {
    "v": 1,
    "seq": 75,
    "t_ms": 51500,
    "type": "agent_said",
    "text": "Your washer is paused with error 4C, which means it isn't getting water. Check that the tap is open and the inlet hose isn't kinked.",
    "kind": "final"
  },
  {
    "v": 1,
    "seq": 76,
    "t_ms": 51550,
    "type": "goal_finished",
    "goal_id": "g-0005",
    "completed": true,
    "summary": "Washer diagnosed: 4C"
  }
];
