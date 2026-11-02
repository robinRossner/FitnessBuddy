import pytest

from apps.fitness.db.types import SessionKind
from apps.fitness.exercises.session_kind import ExerciseUse, infer_session_kind

U = SessionKind


def use(pattern, muscle, sets=3, bodyweight=False):
    return ExerciseUse(pattern, muscle, bodyweight, sets)


BENCH = use("horizontal_push", "chest")
ROW = use("horizontal_pull", "back")
CURL = use("elbow_flexion", "biceps")
SQUAT = use("squat", "quads")
LEG_CURL = use("knee_flexion", "hamstrings")
CALF = use("calf_raise", "calves")
CRUNCH = use("trunk_flexion", "abs")
PULL_UP = use("vertical_pull", "lats", bodyweight=True)
DIP = use("vertical_push", "chest", bodyweight=True)
L_SIT = use("isometric_core", "abs", bodyweight=True)


@pytest.mark.parametrize(
    ("uses", "expected"),
    [
        ([BENCH, ROW, CURL], U.UPPER),
        ([SQUAT, LEG_CURL, CALF], U.LOWER),
        ([BENCH, ROW, SQUAT, LEG_CURL], U.FULL),
        ([BENCH, ROW, CURL, CRUNCH], U.UPPER),  # core is neutral
        ([PULL_UP, DIP, L_SIT, use("vertical_pull", "lats", bodyweight=True)], U.CALISTHENICS),
        ([PULL_UP, BENCH, ROW, CURL], U.UPPER),  # a pull-up in a gym session is not calisthenics
        ([SQUAT, SQUAT, SQUAT, BENCH], U.LOWER),  # 9 of 12 sets = 75 % lower
        ([SQUAT, BENCH, BENCH, BENCH], U.UPPER),  # 25 % lower
        ([CRUNCH], U.OTHER),  # core only
        ([use("cardio", "full_body")], U.OTHER),  # cardio ignored
        ([], U.OTHER),
    ],
)
def test_infer_session_kind(uses, expected):
    assert infer_session_kind(uses) is expected


def test_set_counts_weigh_the_exercises():
    assert infer_session_kind([use("squat", "quads", sets=1), BENCH, ROW]) is U.UPPER
    assert (
        infer_session_kind([use("squat", "quads", sets=8), use("horizontal_push", "chest", 1)])
        is U.LOWER
    )
    assert infer_session_kind([use("squat", "quads", sets=0), BENCH]) is U.UPPER
