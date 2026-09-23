import pytest
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.db import IntegrityError
from django.test import RequestFactory

from ..factories import BatchStageFactory
from ..models import BatchStage

# The fixed v1 workflow graph (TODO-BatchStage.txt, "CANONICAL WORKFLOW GRAPH"),
# seeded by migration 0035_default_load2.
EXPECTED_STAGES = [
    # (shortid, name, from_state, to_state, transfers_batch)
    ("pitch", "Pitch", "", BatchStage.STATE_FERMENTATION, False),
    ("racking", "Racking", BatchStage.STATE_FERMENTATION, BatchStage.STATE_AGING, True),
    ("coarse-filtering", "Coarse Filtering", BatchStage.STATE_AGING, BatchStage.STATE_AGING, True),
    ("fine-filtering", "Fine Filtering", BatchStage.STATE_AGING, BatchStage.STATE_AGING, True),
    ("sterile-filtering", "Sterile Filtering", BatchStage.STATE_AGING, BatchStage.STATE_BOTTLING, True),
    ("complete-batch", "Complete Batch", BatchStage.STATE_BOTTLING, BatchStage.STATE_COMPLETED, False),
]


# ---------- Seeded workflow ----------

@pytest.mark.django_db
def test_the_six_workflow_transitions_are_seeded_in_order():
    stages = [
        (s.shortid, s.name, s.from_state, s.to_state, s.transfers_batch)
        for s in BatchStage.objects.all()
    ]

    assert stages == EXPECTED_STAGES


@pytest.mark.django_db
def test_seeded_stages_are_valid_against_the_model():
    for stage in BatchStage.objects.all():
        stage.full_clean()  # should not raise


@pytest.mark.django_db
def test_filtering_stages_describe_their_micron_rating():
    descriptions = dict(BatchStage.objects.values_list("shortid", "description"))

    assert "micron" in descriptions["coarse-filtering"]
    assert "micron" in descriptions["fine-filtering"]
    assert "micron" in descriptions["sterile-filtering"]


@pytest.mark.django_db
def test_a_stage_can_be_looked_up_by_shortid():
    assert BatchStage.objects.get(shortid="sterile-filtering").to_state == BatchStage.STATE_BOTTLING


@pytest.mark.django_db
def test_the_typo_course_filtering_is_not_seeded():
    assert not BatchStage.objects.filter(name__icontains="course").exists()


# ---------- Model ----------

def test_states_are_the_four_boxes_of_the_workflow_diagram():
    assert [value for value, _ in BatchStage.STATE_CHOICES] == [
        "Fermentation",
        "Aging",
        "Bottling",
        "Completed",
    ]


@pytest.mark.django_db
def test_shortid_is_generated_from_the_name_when_blank():
    stage = BatchStageFactory(name="Cold Crash", shortid="")

    assert stage.shortid == "cold-crash"


@pytest.mark.django_db
def test_an_explicit_shortid_is_kept():
    stage = BatchStageFactory(name="Cold Crash", shortid="crash")

    assert stage.shortid == "crash"


@pytest.mark.django_db
def test_shortid_is_unique():
    with pytest.raises(IntegrityError):
        BatchStageFactory(name="Another Pitch", shortid="pitch")


@pytest.mark.django_db
def test_blank_from_state_means_start():
    assert BatchStage.objects.get(shortid="pitch").get_from_state_display() == "Start"


# ---------- Admin: view-only ----------

@pytest.mark.django_db
def test_batch_stages_are_view_only_in_django_admin():
    superuser = get_user_model().objects.create_superuser("root", "root@example.com", "pw")
    request = RequestFactory().get("/admin/")
    request.user = superuser
    model_admin = admin.site._registry[BatchStage]
    stage = BatchStage.objects.first()

    assert model_admin.has_view_permission(request, stage)
    assert not model_admin.has_add_permission(request)
    assert not model_admin.has_change_permission(request, stage)
    assert not model_admin.has_delete_permission(request, stage)
