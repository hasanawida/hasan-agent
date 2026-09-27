"""Phone target cannot leak into PC tools; approvals remain scoped to device/task."""
import asyncio
import json
import threading

import pytest
from fastapi import HTTPException
from unittest.mock import AsyncMock
from .conftest import wait
from .test_operator import op, pending
from hassan_ai.operator_mode import Operator

PHONE='phone_0123456789abcdef01234567'

def setup_phone(client, monkeypatch):
    hub=client.app.state.phones
    monkeypatch.setattr(hub,'list_devices',lambda:[{'device_id':PHONE,'online':True,'control_enabled':True,'busy':False}])
    monkeypatch.setattr(hub,'claim',AsyncMock(return_value='owner'))
    monkeypatch.setattr(hub,'renew',AsyncMock(return_value='owner'))
    monkeypatch.setattr(hub,'release',AsyncMock())
    monkeypatch.setattr(hub,'command',AsyncMock(return_value={'ok':True,'result':{'nodes':[{'text':'Settings','bounds':[0,0,1,1]}]}}))
    return hub

def start(client,*actions):
    response=client.post('/api/tasks',json={'prompt':'على التلفون\n'+'\n'.join(actions),'kind':'operate','device_id':PHONE})
    response.raise_for_status()
    return response.json()['id']

def test_phone_target_is_persisted_and_inspection_uses_only_selected_device(client,monkeypatch):
    hub=setup_phone(client,monkeypatch)
    tid=start(client,op('phone_inspect'))
    task=wait(client,tid,{'completed','failed'})
    assert task['status']=='completed',task.get('error')
    assert task['device_id']==PHONE
    hub.command.assert_awaited_once_with(PHONE,'task:'+tid,'inspect',{})
    hub.release.assert_awaited_once()
    assert task['evidence'][0]['kind']=='phone'
    assert task['approvals']==[]

def test_phone_mutation_waits_for_approval_and_does_not_claim_another_target(client,monkeypatch):
    hub=setup_phone(client,monkeypatch)
    tid=start(client,op('phone_tap',x=.5,y=.5))
    _,approval=pending(client,tid)
    assert approval['action']=='phone.tap'
    assert approval['payload']['args']['device_id']==PHONE
    hub.command.assert_not_awaited()
    client.post('/api/approvals/'+approval['id'],json={'approve':True})
    task=wait(client,tid,{'completed','failed'})
    assert task['status']=='completed'
    assert [call.args for call in hub.command.await_args_list] == [(PHONE,'task:'+tid,'tap',{'x':.5,'y':.5}), (PHONE,'task:'+tid,'inspect',{}), (PHONE,'task:'+tid,'inspect',{})]

def test_phone_agent_cannot_use_pc_tools_or_switch_device(client,monkeypatch,tmp_path):
    hub=setup_phone(client,monkeypatch)
    tid=start(client,op('write_file',path=str(tmp_path/'unexpected.txt'),content='x'),op('phone_key',key='home',device_id='another-phone'))
    task=wait(client,tid,{'completed','failed'})
    assert task['status']=='failed'
    assert not (tmp_path/'unexpected.txt').exists()
    assert not task['approvals']
    hub.command.assert_not_awaited()

def test_cancellation_releases_phone_lease_and_pending_approval(client,monkeypatch):
    hub=setup_phone(client,monkeypatch)
    tid=start(client,op('phone_key',key='home'))
    pending(client,tid)
    client.post('/api/tasks/'+tid+'/cancel').raise_for_status()
    task=wait(client,tid,{'cancelled'})
    assert task['status']=='cancelled'
    hub.command.assert_not_awaited()
    hub.release.assert_awaited_once()

def test_phone_task_rejects_wrong_mode_unknown_offline_or_busy_device(client,monkeypatch):
    assert client.post('/api/tasks',json={'prompt':'hello','device_id':PHONE}).status_code==422
    assert client.post('/api/tasks',json={'prompt':'hello','kind':'operate','device_id':PHONE}).status_code==404
    hub=setup_phone(client,monkeypatch)
    monkeypatch.setattr(hub,'list_devices',lambda:[{'device_id':PHONE,'online':False,'control_enabled':False}])
    assert client.post('/api/tasks',json={'prompt':'hello','kind':'operate','device_id':PHONE}).status_code==409
    monkeypatch.setattr(hub,'list_devices',lambda:[{'device_id':PHONE,'online':True,'control_enabled':True,'busy':True}])
    assert client.post('/api/tasks',json={'prompt':'hello','kind':'operate','device_id':PHONE}).status_code==409

def test_phone_approval_trust_is_device_specific():
    assert Operator.trust_key('phone_tap',{'device_id':'one'}) != Operator.trust_key('phone_tap',{'device_id':'two'})


def test_handset_failure_is_failed_evidence_and_never_completed(client, monkeypatch):
    hub = setup_phone(client, monkeypatch)
    hub.command.return_value = {"ok": False, "result": {}, "error": "Phone is locked"}
    tid = start(client, op("phone_inspect"))
    task = wait(client, tid, {"completed", "failed"})
    assert task["status"] == "failed"
    assert task["evidence"][0]["ok"] is False
    assert "Phone is locked" in task["evidence"][0]["detail"]
    hub.command.assert_awaited_once()


def test_unconfirmed_mutation_ends_incomplete_without_remaining_actions_or_retry(client, monkeypatch):
    hub = setup_phone(client, monkeypatch)
    hub.command.side_effect = HTTPException(504, "Phone did not confirm this command")
    # Both the duplicate mutation and later read must be skipped after uncertainty.
    tid = start(client, op("phone_tap", x=.4, y=.6), op("phone_tap", x=.4, y=.6), op("phone_inspect"))
    _, approval = pending(client, tid)
    client.post('/api/approvals/' + approval['id'], json={"approve": True, "trust_similar": True}).raise_for_status()
    task = wait(client, tid, {"incomplete", "failed", "completed"})
    assert task["status"] == "incomplete"
    assert "may have executed" in task["error"]
    assert "بدون إعادة المحاولة" in task["decision"]
    assert task["verified"] is None
    assert len(task["evidence"]) == 1 and task["evidence"][0]["ok"] is False
    hub.command.assert_awaited_once_with(PHONE, 'task:' + tid, 'tap', {'x': .4, 'y': .6})
    hub.release.assert_awaited_once()
    assert not [a for a in task["approvals"] if a["status"] == "pending"]


@pytest.mark.parametrize("release_status", [403, 404])
def test_cancellation_stays_cancelled_if_phone_was_revoked_or_reassigned(client, monkeypatch, release_status):
    hub = setup_phone(client, monkeypatch)
    hub.release.side_effect = HTTPException(release_status, "Phone lease is gone")
    tid = start(client, op("phone_key", key="home"))
    _, approval = pending(client, tid)
    client.post('/api/tasks/' + tid + '/cancel').raise_for_status()
    task = wait(client, tid, {"cancelled", "failed"})
    assert task["status"] == "cancelled"
    assert not task["error"]
    assert not [a for a in task["approvals"] if a["status"] == "pending"]
    hub.command.assert_not_awaited()
    assert approval["id"] not in client.app.state.orchestrator._waiters


def test_lease_loss_closes_pending_approval_and_retains_original_failure(client, monkeypatch):
    hub = setup_phone(client, monkeypatch)
    monkeypatch.setattr('hassan_ai.operator_mode.PHONE_LEASE_RENEW_SECONDS', .001)
    fail_lease = threading.Event()

    async def renew(*args):
        while not fail_lease.is_set():
            await asyncio.sleep(.001)
        raise HTTPException(409, "Phone disabled control")

    hub.renew.side_effect = renew
    hub.release.side_effect = HTTPException(404, "Phone revoked during cleanup")
    tid = start(client, op("phone_key", key="home"))
    _, approval = pending(client, tid)
    fail_lease.set()
    task = wait(client, tid, {"failed", "cancelled"})
    assert task["status"] == "failed"
    assert "Phone disabled control" in task["error"]
    assert "cleanup" not in task["error"]
    assert not [a for a in task["approvals"] if a["status"] == "pending"]
    assert approval["id"] not in client.app.state.orchestrator._waiters
    hub.command.assert_not_awaited()
    assert client.post('/api/approvals/' + approval['id'], json={"approve": True}).status_code == 409
