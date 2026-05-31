from sqlalchemy.orm import Session

from app.services import training_data as training_service


def test_record_example_stores_full_input_and_output(db_session: Session) -> None:
    input_text = "finish the firewall cleanup by Friday"
    output_json = '{"summary": "x", "tasks": [], "needs_review": true}'

    example = training_service.record_example(
        db_session,
        task_name="task_extraction",
        input_text=input_text,
        model_output_json=output_json,
        model_profile="task_extraction",
        model_name="gemma4:e2b",
    )

    assert example.id is not None
    assert example.input_text == input_text
    assert example.model_output_json == output_json
    assert example.corrected_output_json is None
    assert example.accepted is False
    assert example.model_profile == "task_extraction"
    assert example.model_name == "gemma4:e2b"
