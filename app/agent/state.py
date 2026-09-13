from __future__ import annotations

from enum import Enum


class WorkflowState(str, Enum):
    CREATED = "created"
    INPUTS_PREPARED = "inputs_prepared"
    WAITING_ADDITIONAL_INPUTS = "waiting_additional_inputs"
    WAITING_IMPORT_APPROVAL = "waiting_import_approval"
    WAITING_VENDOR_RETURNS = "waiting_vendor_returns"
    WAITING_RECONCILIATION_APPROVAL = "waiting_reconciliation_approval"
    WAITING_PAYMENT_APPROVAL = "waiting_payment_approval"
    COMPLETED = "completed"
    FAILED = "failed"


ALLOWED_TRANSITIONS = {
    WorkflowState.CREATED: {WorkflowState.INPUTS_PREPARED, WorkflowState.FAILED},
    WorkflowState.INPUTS_PREPARED: {WorkflowState.WAITING_ADDITIONAL_INPUTS, WorkflowState.FAILED},
    WorkflowState.WAITING_ADDITIONAL_INPUTS: {WorkflowState.WAITING_IMPORT_APPROVAL, WorkflowState.FAILED},
    WorkflowState.WAITING_IMPORT_APPROVAL: {WorkflowState.WAITING_VENDOR_RETURNS, WorkflowState.FAILED},
    WorkflowState.WAITING_VENDOR_RETURNS: {WorkflowState.WAITING_RECONCILIATION_APPROVAL, WorkflowState.FAILED},
    WorkflowState.WAITING_RECONCILIATION_APPROVAL: {WorkflowState.WAITING_PAYMENT_APPROVAL, WorkflowState.FAILED},
    WorkflowState.WAITING_PAYMENT_APPROVAL: {WorkflowState.COMPLETED, WorkflowState.FAILED},
    WorkflowState.COMPLETED: set(),
    WorkflowState.FAILED: set(),
}


class WorkflowStateError(RuntimeError):
    pass


def ensure_state(current: WorkflowState, expected: WorkflowState) -> None:
    if current != expected:
        raise WorkflowStateError(f"Operation requires state {expected.value}; current state is {current.value}")


def ensure_transition(current: WorkflowState, target: WorkflowState) -> None:
    if target not in ALLOWED_TRANSITIONS[current]:
        raise WorkflowStateError(f"Invalid workflow transition: {current.value} -> {target.value}")
