from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv
from sqlalchemy import func, select
from telegram import BotCommand, Update
from telegram.ext import (
    Application,
    ApplicationBuilder,
    ApplicationHandlerStop,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from app.services.audit_pdf import build_audit_pdf
from app.services.content_pdf import build_content_pdf
from app.storage.content_repository import ContentRepository
from app.storage.database import get_session, init_db
from app.storage.models import ContentJob, ContentTokenUsage, SEOProject, WorkflowRun
from app.storage.retention_manager import RetentionManager, StorageLimitError
from app.workflow.content_orchestrator import ContentWorkflowOrchestrator
from app.workflow.orchestrator import WorkflowOrchestrator

PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_PROXY_URL = os.getenv("TELEGRAM_PROXY_URL")
AUDIT_DISPLAY_COUNT = int(os.getenv("TELEGRAM_AUDIT_DISPLAY_COUNT", "5"))
(
    BOOK_APPOINTMENT,
    OUR_SERVICES,
    AVAILABLE_DOCTORS,
    ASK_AI_RECEPTIONIST,
    MY_APPOINTMENTS,
    APPOINTMENT_STATUS,
    PAYMENT-AND-RECEIPTS,
    MAIN_MENU
) = range(9)
logger = logging.getLogger(__name__)
ACTIVE_PROCESSES_KEY = "active_processes"


def _active_processes(application: Application) -> dict[str, list[dict]]:
    """Return the per-user registry of cancellable background work."""
    bot_data = application.bot_data
    return bot_data.setdefault(ACTIVE_PROCESSES_KEY, {})


def _register_process(
    application: Application,
    requester_id: str,
    task: asyncio.Task,
    kind: str,
    record_id: int | None = None,
) -> None:
    processes = _active_processes(application)
    processes.setdefault(requester_id, []).append(
        {"task": task, "kind": kind, "record_id": record_id}
    )


def _unregister_current_process(
    application: Application,
    requester_id: str,
) -> None:
    processes = _active_processes(application)
    current = asyncio.current_task()
    remaining = [
        item for item in processes.get(requester_id, []) if item["task"] is not current
    ]
    if remaining:
        processes[requester_id] = remaining
    else:
        processes.pop(requester_id, None)


async def _discard_content_job(job_id: int) -> None:
    """Delete a cancelled content run and all cascade-owned records."""
    async with get_session() as session:
        job = await session.get(ContentJob, job_id)
        if job is not None:
            await session.delete(job)
            await session.commit()


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return
    await update.message.reply_text(
        "MARINA_AI-RECEPTIONIST Bot is online.\n\nUse /help to see available commands."
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return
    await update.message.reply_text(
        "SEO Automation Commands\n\n"
        "/audit - Start a guided SEO audit\n"
        "/audits - Show the 5 most recent audits\n"
        "/report - Download an audit report as PDF\n"
        "/content - Start guided content generation\n"
        "/review - Manually review saved generated content\n"
        "/contentjobs - Show your 5 most recent content jobs\n"
        "/contentresult - Download research, strategy, and article as PDF\n"
        "/status - Show current workflow status\n"
        "/cancel - Immediately cancel current input or running work"
    )


def _normalise_web_url(value: str) -> str | None:
    value = value.strip()
    if not value:
        return None
    if "://" not in value:
        value = f"https://{value}"
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return None
    if "." not in parsed.hostname:
        return None
    return value


async def _reply_command_error(update: Update, message: str) -> None:
    if update.message is not None:
        await update.message.reply_text(f"{message}\n\nShow all commands: /help")


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return
    requester_id = _requester_id(update)
    async with get_session() as session:
        active_audits = await session.scalar(
            select(func.count())
            .select_from(WorkflowRun)
            .where(
                WorkflowRun.status.in_({"pending", "running"}),
                WorkflowRun.requester_id == requester_id,
            )
        )
        active_content = await session.scalar(
            select(func.count())
            .select_from(ContentJob)
            .where(
                ContentJob.requester_id == requester_id,
                ContentJob.status.in_(
                    {
                        "pending",
                        "strategy_running",
                        "writer_running",
                        "review_running",
                        "needs_revision",
                    }
                ),
            )
        )
        total_tokens = await session.scalar(
            select(func.coalesce(func.sum(ContentTokenUsage.total_tokens), 0))
            .join(ContentJob, ContentJob.id == ContentTokenUsage.content_job_id)
            .where(ContentJob.requester_id == requester_id)
        )
        total_cost = await session.scalar(
            select(func.coalesce(func.sum(ContentTokenUsage.estimated_cost_usd), 0))
            .join(ContentJob, ContentJob.id == ContentTokenUsage.content_job_id)
            .where(ContentJob.requester_id == requester_id)
        )
    await update.message.reply_text(
        f"Status: Online\nActive audits: {active_audits or 0}\n"
        f"Active content jobs: {active_content or 0}\n"
        f"Content tokens used: {total_tokens or 0}\n"
        f"Estimated content cost: ${float(total_cost or 0):.6f}"
    )


async def run_audit(
    application: Application,
    chat_id: int,
    website_url: str,
    progress_message_id: int,
    requester_id: str,
) -> None:
    last_update = 0.0

    async def progress_callback(
        pages_crawled: int = 0,
        errors_count: int = 0,
        queued_count: int = 0,
        current_url: str = "",
    ) -> None:
        nonlocal last_update
        now = time.monotonic()
        if now - last_update < 5:
            return
        last_update = now
        current_display = str(current_url)
        if len(current_display) > 80:
            current_display = current_display[:77] + "..."
        try:
            await application.bot.edit_message_text(
                chat_id=chat_id,
                message_id=progress_message_id,
                text=(
                    "🔎 SEO audit running\n\n"
                    f"Website: {website_url}\nPages crawled: {pages_crawled}\n"
                    f"Errors found: {errors_count}\nPages queued: {queued_count}\n"
                    f"Current URL: {current_display}\n\nStill working..."
                ),
            )
        except Exception:
            logger.debug("Telegram audit progress update failed", exc_info=True)

    orchestrator = WorkflowOrchestrator(progress_callback=progress_callback)
    state = None
    try:
        state = await orchestrator.run(website_url, requester_id=requester_id)
        if state.status != "completed":
            text = (
                f"❌ SEO audit failed\n\nWebsite: {website_url}\n"
                f"Status: {state.status}\n\nPlease verify the URL and try again. "
                "If the problem continues, contact the bot administrator."
            )
        else:
            auditor = state.results.get("website_auditor", {})
            issues = state.results.get("issue_processor", {})
            text = (
                f"✅ SEO audit completed\n\nWebsite: {website_url}\n"
                f"Pages crawled: {auditor.get('pages_crawled', 0)}\n"
                f"Errors: {auditor.get('errors_count', 0)}\n"
                f"SEO issues: {issues.get('issues_created', 0)}\n\n"
                f"Audit ID: {state.workflow_run_id}"
            )
        try:
            await application.bot.edit_message_text(
                chat_id=chat_id, message_id=progress_message_id, text=text
            )
        except Exception:
            logger.warning("Telegram audit completion update failed", exc_info=True)
    except asyncio.CancelledError:
        # Usually the orchestrator catches cancellation while it is still
        # running. Cover the tiny interval after completion but before the
        # Telegram completion message is delivered as well.
        if state is not None and state.workflow_run_id is not None:
            await orchestrator._discard_cancelled_run(state.workflow_run_id)
        raise
    except Exception:
        logger.exception("Telegram audit failed", extra={"website_url": website_url})
        try:
            await application.bot.edit_message_text(
                chat_id=chat_id,
                message_id=progress_message_id,
                text="❌ SEO audit failed unexpectedly. Please try again later.",
            )
        except Exception:
            logger.warning("Telegram audit failure update failed", exc_info=True)
    finally:
        _unregister_current_process(application, requester_id)


async def audit_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.message is None:
        return ConversationHandler.END
    if not context.args:
        await update.message.reply_text(
            "SEO Audit\n\nEnter the website URL. You can use example.com or a full "
            "http:// or https:// URL.\n\nUse /cancel to stop."
        )
        return AUDIT_URL
    if len(context.args) != 1:
        await update.message.reply_text(
            "Enter one complete website URL, such as https://example.com"
        )
        return AUDIT_URL
    website_url = _normalise_web_url(context.args[0])
    if website_url is None:
        await update.message.reply_text(
            "Please provide a valid URL beginning with http:// or https://."
        )
        return AUDIT_URL
    try:
        await RetentionManager().ensure_capacity()
    except StorageLimitError as exc:
        await update.message.reply_text(f"⚠️ Storage limit reached\n\n{exc}")
        return ConversationHandler.END
    progress = await update.message.reply_text(
        f"🔎 SEO audit running\n\nWebsite: {website_url}\n"
        "Pages crawled: 0\nErrors found: 0\n\nStarting crawler..."
    )
    requester_id = _requester_id(update)
    task = context.application.create_task(
        run_audit(
            context.application,
            update.message.chat_id,
            website_url,
            progress.message_id,
            requester_id,
        ),
        update=update,
        name=f"seo-audit:{website_url}",
    )
    _register_process(context.application, requester_id, task, "audit")
    return ConversationHandler.END


async def collect_audit_url(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    context.args = [(update.message.text or "").strip()] if update.message else []
    return await audit_command(update, context)


async def audits_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return
    requester_id = _requester_id(update)
    async with get_session() as session:
        result = await session.execute(
            select(WorkflowRun, SEOProject)
            .join(SEOProject, SEOProject.id == WorkflowRun.project_id)
            .where(
                WorkflowRun.status == "completed",
                WorkflowRun.requester_id == requester_id,
            )
            .order_by(WorkflowRun.completed_at.desc(), WorkflowRun.id.desc())
            .limit(AUDIT_DISPLAY_COUNT)
        )
        rows = result.all()
    if not rows:
        await update.message.reply_text("No completed audits are available.")
        return
    blocks = ["📊 Recent SEO Audits"]
    for index, (audit, project) in enumerate(rows, 1):
        output = audit.output_data or {}
        blocks.append(
            f"{index}. Audit #{audit.id}\n{project.website_url}\n"
            f"{output.get('pages_crawled', 0)} pages\n"
            f"{output.get('issues_created', 0)} issues\nCompleted"
        )
    await update.message.reply_text("\n\n".join(blocks))


AUDIT_ISSUE_GUIDE: dict[str, tuple[str, str, str]] = {
    "Missing title": (
        "On-page SEO",
        "Search engines and users lack a clear page title.",
        "Add a unique, descriptive title tag of roughly 30-60 characters.",
    ),
    "Title longer than 60 characters": (
        "On-page SEO",
        "The title may be truncated in search results.",
        "Shorten the title while retaining the page's primary topic.",
    ),
    "Missing meta description": (
        "On-page SEO",
        "Search engines must generate the search-result description themselves.",
        "Add a specific, persuasive meta description for this page.",
    ),
    "Meta description longer than 160 characters": (
        "On-page SEO",
        "The description may be truncated in search results.",
        "Rewrite it to communicate the main benefit within about 160 characters.",
    ),
    "Missing H1 heading": (
        "Content structure",
        "The page has no primary heading that identifies its main subject.",
        "Add one descriptive H1 that matches the page's intent.",
    ),
    "Multiple H1 headings": (
        "Content structure",
        "Competing primary headings can make the document hierarchy unclear.",
        "Keep one primary H1 and convert subordinate headings to H2/H3.",
    ),
    "Missing canonical tag": (
        "Technical SEO",
        "The preferred version of the URL is not declared.",
        "Add a self-referencing canonical, or point to the intended preferred URL.",
    ),
    "Page has a noindex robots directive": (
        "Indexability",
        "Search engines are instructed not to index this page.",
        "Remove noindex if the page should appear in search; otherwise confirm it is intentional.",
    ),
    "Duplicate title found": (
        "On-page SEO",
        "The same title is used by another crawled page, weakening page differentiation.",
        "Give this page a unique title aligned with its specific search intent.",
    ),
}


def _audit_issue_detail(page: dict, finding: str) -> tuple[str, str, str, str]:
    if finding.endswith("images missing alt text"):
        images = page.get("images_missing_alt") or []
        location = ", ".join(str(item) for item in images[:5]) or "image elements"
        if len(images) > 5:
            location += f" (+{len(images) - 5} more)"
        return (
            "Accessibility / image SEO",
            "Images without alt text are harder for screen readers and image search to understand.",
            location,
            "Add concise, meaningful alt text to informative images; use empty alt text for decorative images.",
        )
    if finding.startswith("HTTP error"):
        return (
            "Technical SEO",
            "The page returned an error response and may be unavailable to users and crawlers.",
            "HTTP response status",
            "Restore the page or redirect the URL to the most relevant working page.",
        )
    nature, impact, action = AUDIT_ISSUE_GUIDE.get(
        finding,
        (
            "SEO",
            "This finding may reduce crawlability, clarity, accessibility, or search performance.",
            "Review the affected element and correct it according to the page's intended purpose.",
        ),
    )
    locations = {
        "Missing title": "HTML <title> element",
        "Title longer than 60 characters": "HTML <title> element",
        "Missing meta description": 'HTML <meta name="description"> element',
        "Meta description longer than 160 characters": 'HTML <meta name="description"> element',
        "Missing H1 heading": "Page <h1> heading",
        "Multiple H1 headings": "Page <h1> headings",
        "Missing canonical tag": 'HTML <link rel="canonical"> element',
        "Page has a noindex robots directive": 'HTML <meta name="robots"> element',
        "Duplicate title found": "HTML <title> element",
    }
    return nature, impact, locations.get(finding, "page source/content"), action


def _format_audit_report(report: dict, audit_id: int) -> list[str]:
    """Build Telegram-safe, human-readable issue details from the raw audit."""
    entries: list[str] = []
    for issue in report.get("issue_details") or []:
        evidence = issue.get("evidence") or {}
        location = "page source/content"
        if evidence.get("image_urls"):
            location = ", ".join(str(item) for item in evidence["image_urls"][:5])
        elif issue.get("issue_type") in {"missing_title", "long_title", "duplicate_title"}:
            location = "HTML <title> element"
        elif issue.get("issue_type") in {
            "missing_meta_description",
            "long_meta_description",
        }:
            location = 'HTML <meta name="description"> element'
        elif issue.get("issue_type") in {"missing_h1", "multiple_h1"}:
            location = "Page <h1> heading(s)"
        elif issue.get("issue_type") == "missing_canonical":
            location = 'HTML <link rel="canonical"> element'
        elif issue.get("issue_type") == "noindex_page":
            location = 'HTML <meta name="robots"> element'
        entries.append(
            f"{len(entries) + 1}. {issue.get('title') or issue.get('issue_type') or 'SEO issue'}\n"
            f"Priority: {str(issue.get('severity') or 'medium').upper()}\n"
            f"Nature: {str(issue.get('category') or 'SEO').replace('_', ' ').title()}\n"
            f"Page: {issue.get('url') or 'Unknown URL'}\n"
            f"Location: {location}\n"
            f"Why it matters: {issue.get('description') or 'This finding can affect search performance.'}\n"
            f"Recommended action: {issue.get('recommendation') or 'Review and correct the affected element.'}"
        )
    if entries:
        return _chunk_audit_entries(report, audit_id, entries)
    for page in report.get("pages") or []:
        for finding in page.get("issues") or []:
            nature, impact, location, action = _audit_issue_detail(page, str(finding))
            entries.append(
                f"{len(entries) + 1}. {finding}\n"
                f"Nature: {nature}\n"
                f"Page: {page.get('url') or 'Unknown URL'}\n"
                f"Location: {location}\n"
                f"Why it matters: {impact}\n"
                f"Recommended action: {action}"
            )
    for error in report.get("errors") or []:
        finding = str(error.get("error") or "Unknown crawl error")
        entries.append(
            f"{len(entries) + 1}. Crawl error\n"
            "Nature: Crawlability / technical SEO\n"
            f"Page: {error.get('url') or 'Unknown URL'}\n"
            "Location: HTTP request or sitemap discovery\n"
            f"Why it matters: The auditor could not inspect this URL ({finding}).\n"
            "Recommended action: Verify the URL, server response, robots rules, and sitemap entry."
        )
    return _chunk_audit_entries(report, audit_id, entries)


def _chunk_audit_entries(report: dict, audit_id: int, entries: list[str]) -> list[str]:
    header = (
        f"Audit #{audit_id} - Actionable SEO Report\n"
        f"Website: {report.get('website') or 'Unknown'}\n"
        f"Pages crawled: {report.get('pages_crawled', 0)}\n"
        f"Issues found: {len(entries)}\n\n"
    )
    if not entries:
        return [header + "No issues were detected by the current audit checks."]
    messages: list[str] = []
    current = header
    for entry in entries:
        if len(entry) > 3400:
            entry = (
                entry[:3340]
                + "\n[Display shortened; the attached JSON contains full evidence.]"
            )
        addition = entry + "\n\n"
        if len(current) + len(addition) > 3900:
            messages.append(current.rstrip())
            current = f"Audit #{audit_id} - Issues continued\n\n"
        current += addition
    messages.append(current.rstrip())
    return messages


async def report_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.message is None:
        return ConversationHandler.END
    if not context.args:
        await update.message.reply_text(
            "Audit PDF\n\nEnter the numeric Audit ID. Use /audits to see your IDs, or /cancel."
        )
        return REPORT_ID
    if len(context.args) != 1 or not context.args[0].isdigit():
        await _reply_command_error(update, "Enter one numeric Audit ID, such as 5.")
        return REPORT_ID
    audit_id = int(context.args[0])
    requester_id = _requester_id(update)
    async with get_session() as session:
        result = await session.execute(
            select(WorkflowRun, SEOProject)
            .join(SEOProject, SEOProject.id == WorkflowRun.project_id)
            .where(
                WorkflowRun.id == audit_id,
                WorkflowRun.requester_id == requester_id,
            )
        )
        row = result.first()
    if row is None:
        await update.message.reply_text(
            f"Audit #{audit_id} is unavailable. Use /audits to see your valid IDs."
        )
        return ConversationHandler.END
    audit, project = row
    if audit.status != "completed":
        await update.message.reply_text(
            f"Audit #{audit_id} is not completed. Current status: {audit.status}."
        )
        return ConversationHandler.END

    value = (audit.output_data or {}).get("report_path")
    path = Path(value) if isinstance(value, str) else None
    if path is not None and not path.is_absolute():
        path = PROJECT_ROOT / path
    path = path.resolve() if path is not None else None
    report_root = (PROJECT_ROOT / "data" / "reports").resolve()
    expected_name = f"audit_{audit_id}.json"
    if (
        path is None
        or report_root not in path.parents
        or path.name != expected_name
        or not path.is_file()
    ):
        await update.message.reply_text(
            f"Report for audit #{audit_id} is unavailable or belongs to the legacy shared "
            "report format. Run a new audit to create an audit-specific report.\n\n"
            "Available recent audits: /audits"
        )
        return ConversationHandler.END
    try:
        with path.open("r", encoding="utf-8") as report_file:
            report = json.load(report_file)
    except (OSError, json.JSONDecodeError):
        await update.message.reply_text(
            f"Report for audit #{audit_id} is unreadable. Please run the audit again."
        )
        return ConversationHandler.END
    report_host = (urlparse(str(report.get("website") or "")).hostname or "").lower()
    project_host = (urlparse(project.website_url).hostname or "").lower()
    if report_host.removeprefix("www.") != project_host.removeprefix("www."):
        await update.message.reply_text(
            f"Report verification failed for audit #{audit_id}; the stored file belongs to "
            "a different website. Please run the audit again."
        )
        return ConversationHandler.END
    try:
        pdf = build_audit_pdf(
            report,
            audit_id,
            project.website_url,
            getattr(audit, "completed_at", None),
        )
    except Exception:
        logger.exception("Unable to build PDF for audit %s", audit_id)
        await update.message.reply_text(
            f"The PDF for audit #{audit_id} could not be generated. Please try again."
        )
        return ConversationHandler.END
    await update.message.reply_document(
        document=pdf,
        filename=pdf.name,
        caption=(
            f"SEO audit #{audit_id} - {project.website_url}\n"
            "Includes each issue, its location, impact, and recommended action."
        ),
    )
    return ConversationHandler.END


async def collect_report_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.args = [(update.message.text or "").strip()] if update.message else []
    return await report_command(update, context)


async def content_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.message is None:
        return ConversationHandler.END
    await init_db()
    try:
        await RetentionManager().ensure_capacity()
    except StorageLimitError as exc:
        await update.message.reply_text(f"⚠️ Storage limit reached\n\n{exc}")
        return ConversationHandler.END
    async with get_session() as session:
        audits = await ContentRepository().list_completed_audits(
            session, _requester_id(update), limit=AUDIT_DISPLAY_COUNT
        )
    if not audits:
        await update.message.reply_text(
            "A completed website audit is required first.\n\nRun /audit <url>."
        )
        return ConversationHandler.END
    context.user_data["content_input"] = {}
    choices = ["Content Generation - Select a website", ""]
    for project, audit in audits:
        output = audit.output_data or {}
        website_display = project.website_url
        if len(website_display) > 250:
            website_display = website_display[:247] + "..."
        choices.append(
            f"Audit #{audit.id} - {website_display}\n"
            f"  {output.get('pages_crawled', 0)} pages, "
            f"{output.get('issues_created', 0)} issues"
        )
    choices.extend(["", "Reply with the Audit ID you want to use (for example: 12)."])
    await update.message.reply_text("\n".join(choices))
    return SELECT_WEBSITE


async def collect_website(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Require an explicit audited-website selection for every content job."""
    if update.message is None:
        return ConversationHandler.END
    value = (update.message.text or "").strip()
    if not value.isdigit():
        await update.message.reply_text(
            "Enter the numeric Audit ID shown in the website list, or /cancel."
        )
        return SELECT_WEBSITE
    async with get_session() as session:
        selected = await ContentRepository().get_owned_completed_audit(
            session, int(value), _requester_id(update)
        )
    if selected is None:
        await update.message.reply_text(
            "That completed audit is not available to you. Choose an Audit ID from the list."
        )
        return SELECT_WEBSITE
    project, audit = selected
    context.user_data["content_input"] = {
        "project_id": project.id,
        "audit_run_id": audit.id,
        "website_url": project.website_url,
    }
    await update.message.reply_text(
        f"Content Generation\n\nSelected website: {project.website_url}\n"
        f"Audit: #{audit.id}\n\n"
        "Step 1/4\n\nEnter your primary keyword:"
    )
    return PRIMARY_KEYWORD


async def collect_primary(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.message is None:
        return ConversationHandler.END
    value = " ".join((update.message.text or "").split())
    if not 2 <= len(value) <= 200:
        await update.message.reply_text(
            "Enter a primary keyword between 2 and 200 characters."
        )
        return PRIMARY_KEYWORD
    context.user_data["content_input"]["primary_keyword"] = value
    await update.message.reply_text(
        "✅ Primary keyword saved.\n\nStep 2/4\n\n"
        "Enter secondary keywords separated with commas."
    )
    return SECONDARY_KEYWORDS


async def collect_secondary(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.message is None:
        return ConversationHandler.END
    values = list(
        dict.fromkeys(
            " ".join(item.split())
            for item in (update.message.text or "").split(",")
            if " ".join(item.split())
        )
    )
    if not values or len(values) > 8 or any(len(item) > 200 for item in values):
        await update.message.reply_text("Enter 1 to 8 valid comma-separated keywords.")
        return SECONDARY_KEYWORDS
    context.user_data["content_input"]["secondary_keywords"] = values
    await update.message.reply_text(
        "✅ Keywords saved.\n\nStep 3/4\n\n"
        "Enter up to 2 competitor URLs, one per line, or type skip. Homepages and specific pages are accepted."
    )
    return COMPETITORS


async def collect_competitors(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    if update.message is None:
        return ConversationHandler.END
    values = list(
        dict.fromkeys(
            line.strip()
            for line in (update.message.text or "").splitlines()
            if line.strip()
        )
    )
    if len(values) == 1 and values[0].lower() in {"skip", "none"}:
        values = []
    valid = []
    for value in values:
        normalised = _normalise_web_url(value)
        if normalised is None:
            await update.message.reply_text(f"Invalid competitor URL: {value}")
            return COMPETITORS
        if normalised not in valid:
            valid.append(normalised)
    if len(valid) > 2:
        await update.message.reply_text(
            "Enter no more than 2 unique competitor URLs, one URL per line, or type skip."
        )
        return COMPETITORS
    context.user_data["content_input"]["competitor_urls"] = valid
    await update.message.reply_text(
        "✅ Competitors saved.\n\nStep 4/4\n\n"
        "Enter the article topic/title, or type auto. The agent will automatically "
        "select and compare the two most relevant pages from the audited website."
    )
    return TOPIC


async def collect_topic(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.message is None:
        return ConversationHandler.END
    value = " ".join((update.message.text or "").split())
    if value.lower() != "auto" and not 3 <= len(value) <= 300:
        await update.message.reply_text(
            "Enter a topic between 3 and 300 characters, or type auto."
        )
        return TOPIC
    data = context.user_data.pop("content_input", None)
    if not data:
        await update.message.reply_text(
            "Content input expired. Start again with /content."
        )
        return ConversationHandler.END
    async with get_session() as session:
        job = await ContentRepository().create_job(
            session,
            data["project_id"],
            data["audit_run_id"],
            data["primary_keyword"],
            data["secondary_keywords"],
            data["competitor_urls"],
            None if value.lower() == "auto" else value,
            requester_id=_requester_id(update),
        )
    progress = await update.message.reply_text(
        _content_progress(data["website_url"], data["primary_keyword"], "strategy", 0)
    )
    requester_id = _requester_id(update)
    task = context.application.create_task(
        run_content(
            context.application,
            update.message.chat_id,
            progress.message_id,
            job.id,
            data["website_url"],
            data["primary_keyword"],
            requester_id,
        ),
        update=update,
        name=f"content-job:{job.id}",
    )
    _register_process(
        context.application,
        requester_id,
        task,
        "content generation",
        job.id,
    )
    return ConversationHandler.END


def _content_progress(
    website: str,
    keyword: str,
    stage: str,
    revision: int,
    detail: str = "",
) -> str:
    marks = {
        "strategy": ("🔄", "⏳", "⏳"),
        "writer": ("✅", "🔄", "⏳"),
        "awaiting_review": ("✅", "✅", "⏳"),
        "reviewer": ("✅", "✅", "🔄"),
        "reviewed": ("✅", "✅", "⚠️"),
        "approved": ("✅", "✅", "✅"),
        "revision": ("✅", "🔄", "🔄"),
        "human_review_required": ("✅", "✅", "⚠️"),
        "failed": ("⚠️", "⚠️", "⚠️"),
    }
    strategist, writer, reviewer = marks.get(stage, ("⚠️", "⚠️", "⚠️"))
    revision_text = f"\nRevision: {revision}" if revision else ""
    return (
        f"📝 Content Generation\n\nWebsite: {website}\nPrimary keyword: {keyword}"
        f"{revision_text}\n\n{strategist} Content Strategist\n"
        f"{writer} Content Writer\n{reviewer} Reviewer\n\n"
        f"Current stage: {detail or stage.replace('_', ' ').title()}"
    )


async def run_content(
    application: Application,
    chat_id: int,
    message_id: int,
    job_id: int,
    website: str,
    keyword: str,
    requester_id: str,
) -> None:
    async def progress(stage: str, detail: str, revision: int) -> None:
        try:
            await application.bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=_content_progress(website, keyword, stage, revision, detail),
            )
        except Exception:
            logger.debug("Telegram content progress update failed", exc_info=True)

    try:
        result = await ContentWorkflowOrchestrator(progress_callback=progress).run(
            job_id
        )
        content = result["content"]
        await application.bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=(
                f"✅ CONTENT GENERATED\n\nContent Job: #{job_id}\nKeyword: {keyword}\n"
                f"Title: {content['title']}\nWords: {result['word_count']}\n"
                f"Drafts saved: {result['draft_count']}\n"
                f"Tokens: {result['usage']['total_tokens']}\n"
                f"Estimated cost: ${float(result['usage']['estimated_cost_usd']):.6f}\n"
                "Review: ⏳ Waiting for your request\n\n"
                f"Use /review {job_id} to review and apply bounded revisions.\n"
                f"Use /contentresult {job_id} to retrieve the saved website and "
                "competitor research, comparisons, strategy, and article."
            ),
        )
    except asyncio.CancelledError:
        await _discard_content_job(job_id)
        raise
    except Exception:
        logger.exception("Telegram content generation failed", extra={"job_id": job_id})
        try:
            await application.bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=(
                    f"❌ Content generation failed\n\nContent Job #{job_id} "
                    "has been saved. Please try again or contact the administrator."
                ),
            )
        except Exception:
            logger.warning("Telegram content failure update failed", exc_info=True)
    finally:
        _unregister_current_process(application, requester_id)


async def review_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.message is None:
        return ConversationHandler.END
    if not context.args:
        async with get_session() as session:
            jobs = await ContentRepository().list_owned_jobs(
                session, _requester_id(update), 20
            )
        waiting = [
            job for job in jobs if job.status in {"awaiting_review", "needs_revision"}
        ]
        if not waiting:
            await update.message.reply_text(
                "You have no generated content waiting for review. Start with /content."
            )
            return ConversationHandler.END
        lines = ["Manual Content Review", "", "Choose a content job:"]
        lines.extend(
            f"Content #{job.id} - {job.project.website_url}\nKeyword: {job.primary_keyword}"
            for job in waiting[:10]
        )
        lines.extend(["", "Reply with the numeric Content Job ID, or /cancel."])
        await update.message.reply_text("\n\n".join(lines))
        return REVIEW_JOB
    if len(context.args) != 1 or not context.args[0].isdigit():
        await update.message.reply_text(
            "Enter one numeric Content Job ID, such as 12, or use /cancel."
        )
        return REVIEW_JOB
    job_id = int(context.args[0])
    async with get_session() as session:
        job = await ContentRepository().get_owned_job(
            session, job_id, _requester_id(update)
        )
    if job is None or job.status not in {"awaiting_review", "needs_revision"}:
        await update.message.reply_text(
            f"Content job #{job_id} is not waiting for review. Use /review to see valid jobs."
        )
        return REVIEW_JOB
    progress = await update.message.reply_text(
        _content_progress(
            job.project.website_url,
            job.primary_keyword,
            "reviewer",
            0,
            "Reviewing saved model drafts",
        )
    )
    requester_id = _requester_id(update)
    task = context.application.create_task(
        run_manual_review(
            context.application,
            update.message.chat_id,
            progress.message_id,
            job.id,
            job.project.website_url,
            job.primary_keyword,
            requester_id,
        ),
        update=update,
        name=f"content-review:{job.id}",
    )
    _register_process(
        context.application,
        requester_id,
        task,
        "content review",
        job.id,
    )
    return ConversationHandler.END


async def collect_review_job(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    context.args = [(update.message.text or "").strip()] if update.message else []
    return await review_command(update, context)


async def run_manual_review(
    application: Application,
    chat_id: int,
    message_id: int,
    job_id: int,
    website: str,
    keyword: str,
    requester_id: str,
) -> None:
    async def progress(stage: str, detail: str, revision: int) -> None:
        try:
            await application.bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=_content_progress(website, keyword, stage, revision, detail),
            )
        except Exception:
            logger.debug("Telegram manual-review progress update failed", exc_info=True)

    try:
        result = await ContentWorkflowOrchestrator(
            progress_callback=progress
        ).review(job_id)
        sections = [
            f"Manual review completed for content #{job_id}",
            f"Final status: {result['status'].replace('_', ' ').title()}",
            f"Revisions used: {result.get('revision_count', 0)}",
        ]
        if result["status"] == "human_review_required":
            sections.append(
                "Automatic revisions reached their safe limit. A person should review the remaining feedback."
            )
        for review in result["reviews"]:
            provider = str(review.get("provider") or "unknown").title()
            issues = [str(item) for item in review.get("issues") or []]
            if issues:
                details = "\n".join(f"- {item}" for item in issues)
            else:
                details = "- No review errors."
            sections.append(
                f"{provider} draft: {str(review.get('status') or '').replace('_', ' ').title()}\n{details}"
            )
        text = "\n\n".join(sections)
        if len(text) > 3900:
            text = text[:3830] + "\n\nFull feedback is saved in /contentresult."
        await application.bot.edit_message_text(
            chat_id=chat_id, message_id=message_id, text=text
        )
    except asyncio.CancelledError:
        await _discard_content_job(job_id)
        raise
    except Exception:
        logger.exception("Telegram manual review failed", extra={"job_id": job_id})
        await application.bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=(
                f"Manual review failed for content #{job_id}. "
                "The saved drafts were preserved; please try again later."
            ),
        )
    finally:
        _unregister_current_process(application, requester_id)


async def cancel_content(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.message is None:
        return ConversationHandler.END
    context.user_data.pop("content_input", None)
    requester_id = _requester_id(update)
    processes = _active_processes(context.application)
    active = list(processes.get(requester_id, []))
    cancelled = []
    for item in active:
        task = item["task"]
        if not task.done() and task.cancel():
            cancelled.append(item)
    if cancelled:
        await asyncio.gather(
            *(item["task"] for item in cancelled),
            return_exceptions=True,
        )
        # The worker performs its own cleanup. This is an idempotent fallback
        # for shutdown/race cases where it could not finish that cleanup.
        for item in cancelled:
            if item["kind"].startswith("content") and item["record_id"] is not None:
                await _discard_content_job(item["record_id"])
        names = ", ".join(sorted({item["kind"] for item in cancelled}))
        await update.message.reply_text(
            f"Cancelled: {names}. No cancelled run or partial report was saved."
        )
    else:
        await update.message.reply_text("Current input cancelled. No process was running.")
    return ConversationHandler.END


async def cancel_text_alias(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Support ?cancel everywhere while preventing further message handling."""
    await cancel_content(update, context)
    raise ApplicationHandlerStop


def _requester_id(update: Update) -> str:
    """Return a stable owner identifier and never use shared records as a fallback."""
    if update.effective_user is not None:
        return str(update.effective_user.id)
    if update.effective_chat is not None:
        return f"chat:{update.effective_chat.id}"
    raise ValueError("The Telegram requester could not be identified.")


async def content_jobs_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    del context
    if update.message is None:
        return
    async with get_session() as session:
        jobs = await ContentRepository().list_owned_jobs(
            session, _requester_id(update), AUDIT_DISPLAY_COUNT
        )
    if not jobs:
        await update.message.reply_text("You do not have any saved content jobs.")
        return
    blocks = ["Your recent content jobs"]
    for job in jobs:
        blocks.append(
            f"Content #{job.id} | {job.status}\n{job.project.website_url}\n"
            f"Keyword: {job.primary_keyword}"
        )
    blocks.append("Retrieve one with /contentresult <id>.")
    await update.message.reply_text("\n\n".join(blocks))


async def content_result_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    if update.message is None:
        return ConversationHandler.END
    if not context.args:
        await update.message.reply_text(
            "Content PDF\n\nEnter the numeric Content Job ID. Use /contentjobs to see your IDs, or /cancel."
        )
        return CONTENT_RESULT_ID
    if len(context.args) != 1 or not context.args[0].isdigit():
        await _reply_command_error(update, "Enter one numeric Content Job ID, such as 12.")
        return CONTENT_RESULT_ID
    job_id = int(context.args[0])
    async with get_session() as session:
        job = await ContentRepository().get_owned_job(
            session, job_id, _requester_id(update)
        )
    if job is None:
        await update.message.reply_text(
            f"Content job #{job_id} is unavailable. Use /contentjobs to see your IDs."
        )
        return ConversationHandler.END

    strategy_record = max(job.strategies, key=lambda item: item.id, default=None)
    strategy = strategy_record.strategy_data if strategy_record else {}
    final_version = next(
        (item for item in job.versions if item.id == job.final_version_id), None
    )
    if final_version is None and job.versions:
        final_version = max(job.versions, key=lambda item: item.version_number)
    comparison = strategy.get("competitor_comparison") or {}
    website_comparison = strategy.get("website_reference_comparison") or {}
    page_lines = [
        f"- {item.get('title') or item.get('url')} | {item.get('word_count', 0)} words | "
        f"quality {item.get('quality_score', 0)}/100 | {item.get('content_nature')} | "
        f"{item.get('wording_style')}"
        for item in comparison.get("pages", [])
    ]
    website_page_lines = [
        f"- {item.get('title') or item.get('url')} | {item.get('word_count', 0)} words | "
        f"quality {item.get('quality_score', 0)}/100 | {item.get('content_nature')}"
        for item in website_comparison.get("pages", [])
    ]
    summary = (
        f"Content job #{job.id}: {job.status}\n"
        f"Website: {job.project.website_url}\nKeyword: {job.primary_keyword}\n"
        f"Target length: {strategy.get('recommended_word_count', 'not available')} words\n\n"
        "Selected website-page comparison\n"
        + ("\n".join(website_page_lines) or "No website-page comparison is available.")
        + "\n\nCompetitor comparison\n"
        + ("\n".join(page_lines) or "No competitor comparison is available.")
    )
    result_payload = {
        "job_id": job.id,
        "status": job.status,
        "website": job.project.website_url,
        "primary_keyword": job.primary_keyword,
        "competitor_urls": [item.url for item in job.competitors],
        "strategy": strategy,
        "drafts": [
            {
                "version": item.version_number,
                "provider": (item.content or {}).get("data_source", "unknown"),
                "word_count": item.word_count,
                "content": item.content,
            }
            for item in sorted(job.versions, key=lambda value: value.version_number)
        ],
        "reviews": [item.feedback for item in job.reviews],
        "final_content": final_version.content if final_version else None,
    }
    try:
        research_file = build_content_pdf(result_payload)
    except Exception:
        logger.exception("Unable to build content PDF for job %s", job.id)
        await update.message.reply_text(
            f"The PDF for content job #{job.id} could not be generated. Please try again."
        )
        return ConversationHandler.END
    await update.message.reply_document(
        document=research_file,
        filename=research_file.name,
        caption=(
            summary[:800]
            + "\n\nFull research, strategy, drafts, reviews, and final article are in the PDF."
        ),
    )
    return ConversationHandler.END


async def collect_content_result_id(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    context.args = [(update.message.text or "").strip()] if update.message else []
    return await content_result_command(update, context)


async def unknown_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    del context
    if update.message is not None:
        await update.message.reply_text(
            "Unknown or invalid command.\n\nShow all commands: /help"
        )


async def unexpected_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    del context
    if update.message is not None:
        await update.message.reply_text(
            "I could not interpret that message.\n\nShow all commands: /help"
        )


async def _scheduled_cleanup(context: ContextTypes.DEFAULT_TYPE) -> None:
    await RetentionManager().cleanup()


async def _post_init(application: Application) -> None:
    await init_db()
    retention = RetentionManager()
    await retention.discard_incomplete()
    await retention.cleanup()
    await application.bot.set_my_commands(
        [
            BotCommand("audit", "Run a guided website SEO audit"),
            BotCommand("audits", "List your recent audits"),
            BotCommand("report", "Download an audit PDF"),
            BotCommand("content", "Generate content with guided inputs"),
            BotCommand("review", "Manually review generated content"),
            BotCommand("contentjobs", "List your recent content jobs"),
            BotCommand("contentresult", "Download a saved content result"),
            BotCommand("status", "Show workflow status"),
            BotCommand("help", "Show all commands"),
            BotCommand("cancel", "Cancel current input or running work"),
        ]
    )
    if application.job_queue is not None:
        application.job_queue.run_repeating(
            _scheduled_cleanup, interval=3600, first=3600, name="retention-cleanup"
        )


def build_application() -> Application:
    if not TELEGRAM_BOT_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not configured in the .env file.")
    builder = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).post_init(_post_init)
    if TELEGRAM_PROXY_URL:
        builder.proxy(TELEGRAM_PROXY_URL).get_updates_proxy(TELEGRAM_PROXY_URL)
    application = builder.build()
    application.add_handler(
        MessageHandler(filters.Regex(r"^\?cancel\s*$"), cancel_text_alias),
        group=-1,
    )
    application.add_handler(
        ConversationHandler(
            entry_points=[CommandHandler("review", review_command)],
            states={
                REVIEW_JOB: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, collect_review_job)
                ]
            },
            fallbacks=[CommandHandler("cancel", cancel_content)],
            allow_reentry=True,
        )
    )
    application.add_handler(
        ConversationHandler(
            entry_points=[CommandHandler("audit", audit_command)],
            states={
                AUDIT_URL: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, collect_audit_url)
                ]
            },
            fallbacks=[CommandHandler("cancel", cancel_content)],
            allow_reentry=True,
        )
    )
    application.add_handler(
        ConversationHandler(
            entry_points=[CommandHandler("report", report_command)],
            states={
                REPORT_ID: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, collect_report_id)
                ]
            },
            fallbacks=[CommandHandler("cancel", cancel_content)],
            allow_reentry=True,
        )
    )
    application.add_handler(
        ConversationHandler(
            entry_points=[CommandHandler("contentresult", content_result_command)],
            states={
                CONTENT_RESULT_ID: [
                    MessageHandler(
                        filters.TEXT & ~filters.COMMAND, collect_content_result_id
                    )
                ]
            },
            fallbacks=[CommandHandler("cancel", cancel_content)],
            allow_reentry=True,
        )
    )
    application.add_handler(
        ConversationHandler(
            entry_points=[CommandHandler("content", content_command)],
            states={
                SELECT_WEBSITE: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, collect_website)
                ],
                PRIMARY_KEYWORD: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, collect_primary)
                ],
                SECONDARY_KEYWORDS: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, collect_secondary)
                ],
                COMPETITORS: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, collect_competitors)
                ],
                TOPIC: [MessageHandler(filters.TEXT & ~filters.COMMAND, collect_topic)],
            },
            fallbacks=[CommandHandler("cancel", cancel_content)],
            allow_reentry=True,
        )
    )
    application.add_handler(CommandHandler("cancel", cancel_content))
    for command, callback in (
        ("start", start_command),
        ("help", help_command),
        ("status", status_command),
        ("audits", audits_command),
        ("contentjobs", content_jobs_command),
    ):
        application.add_handler(CommandHandler(command, callback))
    application.add_handler(MessageHandler(filters.COMMAND, unknown_command))
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, unexpected_text)
    )
    return application


def main() -> None:
    build_application().run_polling()


if __name__ == "__main__":
    main()
