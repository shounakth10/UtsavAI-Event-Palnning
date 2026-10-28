from services.export import generate_ics, generate_pdf, generate_whatsapp_summary


def test_ics_has_one_event_and_two_day_alarm_per_task(make_plan):
    plan = make_plan()

    calendar_text = generate_ics(plan)

    assert calendar_text.count("BEGIN:VEVENT") == len(plan.timeline)
    assert calendar_text.count("BEGIN:VALARM") == len(plan.timeline)
    assert "TRIGGER:-P2D" in calendar_text


def test_pdf_export_returns_a4_pdf(make_plan):
    pdf_content = generate_pdf(make_plan())

    assert pdf_content.startswith(b"%PDF-")
    assert len(pdf_content) > 1000


def test_pdf_export_supports_devanagari_text(make_plan):
    plan = make_plan()
    plan.event.title = "पुण्यातील मराठी लग्न"
    plan.event.summary = "परंपरा आणि आधुनिक नियोजनाचा सुंदर संगम."

    pdf_content = generate_pdf(plan)

    assert pdf_content.startswith(b"%PDF-")
    assert len(pdf_content) > 1000


def test_whatsapp_summary_is_short_and_includes_key_details(make_plan):
    plan = make_plan()

    summary = generate_whatsapp_summary(plan)

    assert plan.event.title in summary
    assert plan.event.date in summary
    assert len(summary) < 2000
