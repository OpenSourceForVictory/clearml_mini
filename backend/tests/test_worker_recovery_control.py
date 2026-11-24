import pytest
from worker.disconnection import DisconnectionClock
from worker.recovery_control import RecoveryControl


def session(tolerance=None):
    return RecoveryControl(DisconnectionClock('e','g','boot',tolerance,0))


def reply(**changes):
    return {'execution_id':'e','generation':'g','sequence':1,'action':'track','duplicate':False,**changes}


def apply(control, response=None, **changes):
    args={'sequence':1,'requested_at':1,'received_at':2,'persist':lambda value:None}
    args.update(changes)
    return control.apply(response if response is not None else reply(),**args)


def test_control_persist_restore_and_no_receipt_authority():
    control=session();saved=[]
    assert apply(control,persist=saved.append)
    restored=RecoveryControl.restore(saved[0],execution_id='e',generation='g',boot_id='boot')
    assert restored.clock.sequence==1 and restored.directive=='track'
    with pytest.raises(ValueError):apply(restored,{'digest':'receipt','reconciled':False})


@pytest.mark.parametrize('changes',[{'execution_id':'other'},{'generation':'other'},
    {'sequence':2},{'sequence':True},{'duplicate':1},{'action':'resume'}])
def test_invalid_reply_does_not_change_local_authority(changes):
    control=session();before=control.snapshot()
    with pytest.raises(ValueError):apply(control,reply(**changes))
    assert control.snapshot()==before


@pytest.mark.parametrize('directive',['stop','inspect','retain'])
def test_irreversible_directive_survives_restart(directive):
    control=session()
    assert apply(control,reply(action=directive,reason='original'))
    restored=RecoveryControl.restore(control.snapshot(),execution_id='e',generation='g',boot_id='boot')
    assert not apply(restored,reply(sequence=2),sequence=2,requested_at=3,received_at=4)
    assert restored.directive==directive


def test_duplicate_and_old_response_do_not_refresh_control_clock():
    control=session()
    assert not apply(control,reply(duplicate=True))
    assert control.clock.confirmed_at==0
    assert apply(control)
    assert not apply(control,received_at=20)
    assert control.clock.confirmed_at==1


@pytest.mark.parametrize('received',[10,11,365*86400])
def test_response_after_finite_deadline_cannot_revive_execution(received):
    control=session(10);saved=[]
    assert apply(control,received_at=received,persist=saved.append)
    assert control.directive=='stop' and control.reason=='disconnection_timeout'
    assert control.clock.sequence==0
    restored=RecoveryControl.restore(saved[0],execution_id='e',generation='g',boot_id='boot')
    assert restored.clock.stop_reason=='disconnection_timeout'


def test_persistence_failure_cannot_grant_tracking():
    control=session();before=control.snapshot()
    def broken(value):raise OSError('disk unavailable')
    with pytest.raises(OSError):apply(control,persist=broken)
    assert control.snapshot()==before


def test_machine_reboot_does_not_reuse_old_monotonic_clock():
    original=session()
    control=RecoveryControl.restore(original.snapshot(),execution_id='e',generation='g',boot_id='new-boot')
    assert apply(control)
    assert control.directive=='stop' and control.reason=='node_restarted'


def test_duplicate_stop_overrides_previous_tracking():
    control=session();apply(control)
    assert apply(control,reply(duplicate=True,action='stop',reason='cancelled'))
    assert control.directive=='stop'


def test_invalid_persisted_directive():
    with pytest.raises(ValueError):RecoveryControl(session().clock,directive='restart')
