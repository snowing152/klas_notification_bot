import asyncio
import logging
import os

if __name__ == "__main__":
    # Before anything else under app/ is imported: app/config.py raises at
    # import when BOT_TOKEN, ADMIN_ID or DATA_DIR is wrong, and with logging set
    # up only at the bottom of this file that traceback - the failure a deploy
    # is most likely to hit - reached Railway as unlabelled lines of stderr.
    from app.utils.log_format import configure_logging

    configure_logging(os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs"))

from app.bot import dp, bot, setup_handlers
from app.database.database import init_db
from app.services.announcements import announce_pending
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

        # Tell users about features added since they last heard from the bot.
        # A task rather than an await: nothing about polling should wait on it.
        asyncio.create_task(announce_pending())

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
                logging.error(
                    f"Error in main loop: {task.exception()}",
                    exc_info=task.exception(),
                )
    except Exception as e:
        # Re-raised so the process exits non-zero: swallowing it here would let a
        # failed start look like a clean shutdown, and the ON_FAILURE restart
        # policy would never kick in. The traceback follows from the excepthook.
        logging.error(f"Error in main loop: {e}")
        raise
    finally:
        await close_qr_session()
        await bot.session.close()


if __name__ == "__main__":
    logging.info("Starting bot...")
    asyncio.run(main())
    logging.info("Program finished!")
