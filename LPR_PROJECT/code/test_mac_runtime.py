"""Regression tests for native Mac LOG decoding and failure detection."""
import pytest
import run_integrated

@pytest.mark.parametrize('encoding',['utf-8','utf-16','utf-16-le'])
def test_completed_native_log_encodings(tmp_path,encoding):
    p=tmp_path/'native.log'
    p.write_bytes('LTspice 17\nTotal elapsed time: 1 seconds\n'.encode(encoding))
    assert run_integrated.read_log(p)['native_log_completion_observed']

@pytest.mark.parametrize('message',['Fatal error: bad model','Timestep too small','Simulation aborted'])
def test_native_failure_is_not_hidden_by_completion(tmp_path,message):
    p=tmp_path/'native.log'
    p.write_bytes(('LTspice 17\n'+message+'\nTotal elapsed time: 1 seconds\n').encode('utf-16-le'))
    with pytest.raises(ValueError):run_integrated.read_log(p)
