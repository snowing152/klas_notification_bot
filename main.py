import asyncio
import logging
from app.bot import dp, bot, setup_handlers
from app.database.database import init_db
from app.services.notifications import start_notification_service
from app.services.qr import close_session as close_qr_session
from app.menu import initialize_bot_menu


async def main():
    # Startup lives inside the try as well: a failure here (an unmounted volume
    # making init_db unable to open the database, say) used to escape before the
    # cleanup below was armed, so the bot session was left open and the real
    # traceback arrived buried under "Unclosed client session" noise.
    try:
        await initialize_bot_menu()

        # Initialize database
        await init_db()

        # Setup all handlers
        setup_handlers(dp)

        # Create tasks for both the notification service and bot polling
        notification_task = asyncio.create_task(start_notification_service())
        polling_task = asyncio.create_task(dp.start_polling(bot))

        # Stop as soon as either task finishes. Waiting for *both* meant a SIGTERM
        # stopped polling but left the notification loop running forever, so the
        # process hung and the cleanup below never ran.
        done, pending = await asyncio.wait(
            {notification_task, polling_task}, return_when=asyncio.FIRST_COMPLETED
        )

        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)

        for task in done:
            if task.exception():
                logging.error(f"Error in main loop: {task.exception()}")
    except Exception as e:
        # Re-raised so the process exits non-zero: swallowing it here would let a
        # failed start look like a clean shutdown, and the ON_FAILURE restart
        # policy would never kick in.
        logging.error(f"Error in main loop: {e}")
        raise
    finally:
        await close_qr_session()
        await bot.session.close()


if __name__ == "__main__":
    import os
    import sys
    import platform

    # Configure logging first. stdout is always a handler: a PaaS such as Railway
    # collects logs from the process output, and logging only to a file inside an
    # ephemeral container means the dashboard shows nothing and the file is lost
    # on the next deploy.
    handlers = [logging.StreamHandler(sys.stdout)]

    # Keep the on-disk log for self-hosted (systemd) runs, where it survives.
    on_railway = bool(os.getenv("RAILWAY_ENVIRONMENT"))
    if platform.system() == "Linux" and not on_railway:
        log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
        os.makedirs(log_dir, exist_ok=True)
        handlers.append(
            logging.FileHandler(
                os.path.join(log_dir, "kwbot.log"), mode="a", encoding="UTF-8"
            )
        )

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=handlers,
    )

    logging.info("Starting bot...")
    asyncio.run(main())
    logging.info("Program finished!")
