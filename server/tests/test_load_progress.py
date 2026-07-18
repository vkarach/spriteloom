import io

from server import models
from server.instruct import _report_tqdm


def test_load_progress_visible_during_factory():
    models.reset()
    seen = {}

    def factory():
        seen["during"] = models.load_progress()
        return object()

    models.register("m", factory)
    assert models.load_progress() is None
    models.get("m")
    assert seen["during"] == 0.0
    assert models.load_progress() is None  # cleared even though no reports
    assert models.is_ready("m")
    models.reset()


def test_load_progress_cleared_after_factory_failure():
    models.reset()
    models.register("bad", lambda: (_ for _ in ()).throw(RuntimeError("x")))
    try:
        models.get("bad")
    except RuntimeError:
        pass
    assert models.load_progress() is None
    models.reset()


def test_report_tqdm_nested_bars_yield_one_fraction():
    import tqdm
    import tqdm.auto
    vals = []
    with _report_tqdm(vals.append):
        outer = tqdm.tqdm(total=2, file=io.StringIO())
        inner = tqdm.auto.tqdm(total=4, file=io.StringIO())
        inner.update(2)          # half of the first outer item -> 0.25
        inner.close()
        outer.update(1)          # first outer item done -> 0.5
        outer.close()
    assert any(abs(v - 0.25) < 1e-6 for v in vals)
    assert abs(vals[-1] - 0.5) < 1e-6
    # the patch must not leak outside the context
    assert "Mirror" not in tqdm.tqdm.__name__


def test_report_tqdm_ignores_totalless_bars():
    import tqdm
    vals = []
    with _report_tqdm(vals.append):
        bar = tqdm.tqdm(file=io.StringIO())  # no total (download-style)
        bar.update(3)
        bar.close()
    assert all(v == 0.0 for v in vals)
