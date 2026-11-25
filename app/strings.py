import logging
from enum import Enum


class Language(Enum):
    EN = "English 🇬🇧"
    KO = "한국어 🇰🇷"
    RU = "Русский 🇷🇺"


class Strings:
    _strings: dict[Language, dict[str, str]] = {
        Language.EN: {
            "test_string": "Welcome, Test User!",
            "nonexistent_key": "Nonexistent key",
            "welcome": """Welcome, {name}! 👋
I'm a helper bot for Kwangwoon University students 🏫
I track your KLAS assignments and remind you before deadlines 🧭

Try the buttons below, or:
• /account - connect KLAS and the library
• /show - see what's due
• /menu, /news - dining and campus news

More options live in the menu button in the bottom left of the chat.

If something's not working, message me @bulochkaskefirochkom 💬 I'll sort it out quickly!
""",
            "enter_username": "🎓 Please enter your student ID (I'll keep this secure)",
            "enter_password": "🔑 Please enter your KLAS password (this will be encrypted)",
            "enter_phone_number": "📱 Please enter your phone number",
            "library_enter_username": "🎓 Please enter your library account username",
            "library_enter_password": "🔑 Please enter your library account password",
            "invalid_credentials": "Hmm, those credentials don't seem to match our records. Please check your username and password, then try /register again 🔄",
            "library_login_failed": "We couldn't log you into the library with those details. Please verify your username, password and phone number, then try /lregister again 🔄",
            "library_login_phone_mismatch": "The library only accepts logins from the phone number registered on your library account. Please double-check the phone number and try /lregister again 🔄",
            "registration_successful": "🎉 You're all set! I can already see your KLAS assignments and will remind you before each deadline. It'll be quiet for a bit while I get familiar with what's already on your plate - that's normal, not a bug.\n\nNext: open /account to connect your library card too, for QR passes and book search.",
            "library_registration_successful": "🎉 Library connected! Grab your QR pass with /qr, or search the catalog from /account → 🔍 Find a book.",
            "registration_failed": "We couldn't complete your registration. Please check your details and try again with /register 🔄",
            "failed_to_save_credentials": "We had a small issue saving your credentials. Please try again later.",
            "unregistered": "You've successfully unregistered from the bot. You can always register again! 👋",
            "register_attempts_limited": "Too many failed logins for this student number. KLAS locks an account after a few wrong passwords, so I'm pausing /register for it for an hour. Please try again later 🔒",
            "delete_data_failed": "I couldn't delete all of your data. Please run /unregister again, and tell the bot admin if it keeps failing ⚠️",
            "need_to_register": "This feature requires registration. Start with /register - it only takes a minute! ✨",
            "no_assignments": "🎉 Great news! You have no upcoming assignments",
            "failed_to_fetch_student_info": "We couldn't retrieve your student information right now. Let's try again later!",
            "unexpected_error": "Oops! We encountered a small issue. Please try again in a few moments! 🔄",
            "menu_only_hint": "I only understand the menu buttons. Please choose an option below.",
            "callback_error": "Please tap the buttons one at a time 😊",
            "donate_title": "Support this project with a coffee! ☕",
            "donate_description": "Your contribution helps keep this bot running and improving. Thank you for your support!",
            "choose_donation_amount": "Select an amount you'd like to contribute:",
            "successful_payment": "✅ Thank you for your generous support! Your contribution helps make this bot better for everyone.",
            "refund_error": "To request a refund, please reply directly to the payment message.",
            "refund_success": "✅ Your refund has been processed successfully. Thank you for your interest in supporting the bot.",
            "refund_error_message": "We couldn't process your refund right now. Please try again later.",
            "library_user_not_found": "You'll need to connect your library account first. Please use the /lregister command to get started.",
            "failed_to_fetch_news": "We couldn't load the latest news right now. Let's try again later.",
            "choose_news_type": "What type of news would you like to see?",
            "please_enter_book_name": "Please enter the name of the book you're looking for after the /search command.",
            "no_books_found": "I couldn't find any books matching that title. Double-check the spelling and try again! 📚",
            "show_header": "📋 Tasks",
            "show_filter_all": "📋 All",
            "show_filter_lectures": "📚 Lectures",
            "show_filter_assignments": "📝 Assignments",
            "show_day_today": "Today",
            "show_day_tomorrow": "Tomorrow",
            "show_day_date": "{weekday}, {day} {month} · in {count} days",
            "show_later": "🗓 Later ({count})",
            "show_in_time": "in {time_str}",
            "weekday_short_0": "Mon",
            "weekday_short_1": "Tue",
            "weekday_short_2": "Wed",
            "weekday_short_3": "Thu",
            "weekday_short_4": "Fri",
            "weekday_short_5": "Sat",
            "weekday_short_6": "Sun",
            "month_short_1": "Jan",
            "month_short_2": "Feb",
            "month_short_3": "Mar",
            "month_short_4": "Apr",
            "month_short_5": "May",
            "month_short_6": "Jun",
            "month_short_7": "Jul",
            "month_short_8": "Aug",
            "month_short_9": "Sep",
            "month_short_10": "Oct",
            "month_short_11": "Nov",
            "month_short_12": "Dec",
            "time_days": "{count}d",
            "time_hours": "{count}h",
            "time_minutes": "{count}m",
            "too_many_messages": "I see you're sending messages quickly! Please pause briefly so I can catch up with your requests.",
            "student_info": """📚 Student Profile:
👨‍🎓 Name: {name} (ID: {uid})
Major: {major}
Year: {grade} | Semester: {semester}
🎯 Credits Summary:
• Total: {total_credits}
• Major: {major_credits_total}
• Elective: {elective_credits_total}
• GPA: {average_score} 📈""",
            "school_food_info": """
🍳 <Breakfast Special - 1000 KRW>
Available 8:30AM - 9:30AM at 복지관 2nd floor

🍔 <Lunch Options - 6000 KRW>
Available 11:30AM - 2:00PM at 복지관 2nd floor

🍴 <Food Court Variety - 8000 KRW>
Available 11:30AM - 2:00PM at 연구관 B1 floor

All options are buffet-style! Pay once and enjoy as much as you like 🍴
""",
            "school_closed_on_weekend": "The university cafeterias are closed on weekends. Looking forward to serving you again on Monday! 🍽️",
            "foreigners_news": "🌏 International News",
            "all_news": "📰 All News",
            "tomorrow_menu": "🗓️ Tomorrow's Menu",
            "info": "ℹ️ More Info",
            "read_more": "📖 Read Full Article",
            "language_changed": "✅ Perfect! Your language preference has been updated.",
            "language_change_failed": "Please use /register first so I can save your language preference",
            "language_choice": "🌐 Choose your preferred language",
            "school_food_menu_header": "🍴 {day}'s Dining Options 🍴\n\n",
            "input_field_placeholder": "Choose an option...",
            "button_todos": "📋 Tasks",
            "button_qr": "📱 QR",
            "reminder_header": "{emoji} Less than {hours}h left",
            "new_assignments_header": "🆕 New in KLAS",
            "settings_header": """⚙️ Notification settings

Tap a line to switch it.""",
            "settings_new_assignments": "🆕 New assignments: {state}",
            "settings_deadline_alerts": "⏰ Deadline reminders: {state}",
            "settings_urgent_only": "🎯 Urgent only (6/3/1h): {state}",
            "settings_quiet_hours": "🌙 Quiet hours {start}:00-{end}:00: {state}",
            "settings_language_button": "🌍 Language",
            "state_on": "on",
            "state_off": "off",
            "announcement_enable": "✅ Turn it on",
            "announcement_dismiss": "🙅 No thanks",
            "announcement_enabled": "✅ Turned on. You can change it any time in /settings.",
            "announcement_dismissed": "🙅 Left off. You can turn it on any time in /settings.",
            "credentials_expired": "🔐 I can't log into KLAS with your saved password anymore. If you changed it (KW asks for that every few months), run /register to save the new one - until then your deadline notifications are paused.",
            "klas_login_required": "🔐 I need you to log in to KLAS again. KLAS now asks you to approve every login in the KLAS phone app: tap the button below, then approve the login on your phone. Until then your deadline notifications are paused.",
            "klas_push_sent": "📲 Open the KLAS app on your phone and approve the login. I'll wait for 2 minutes.",
            "klas_login_success": "✅ Logged in to KLAS. Your deadline notifications are back on.",
            "klas_push_timeout": "⌛ The login was not approved in time. Tap the button to try again.",
            "klas_no_push_device": "📱 KLAS asks you to approve the login in the KLAS phone app, but no phone is registered for your account. Install the KLAS app, log in there once, then tap the button.",
            "klas_login_in_progress": "⏳ A login is already waiting for your approval in the KLAS app.",
            "klas_login_failed": "😕 I couldn't log in to KLAS right now. Please try again in a few minutes.",
            "klas_unavailable": "😕 KLAS is not answering right now. Please try again in a few minutes.",
            "button_klas_relogin": "🔑 Log in to KLAS",
            "account_header": "👤 Your account",
            "account_klas_connected": "✅ KLAS: {username}",
            "account_klas_missing": "◻️ KLAS: not connected - connect it to see assignments, get deadline reminders, and check your student info",
            "account_library_connected": "✅ Library: {username}",
            "account_library_missing": "◻️ Library: not connected - connect it to generate your QR pass and search the library catalog",
            "account_login_klas": "💁‍♂️ Log in to KLAS",
            "account_relogin_klas": "🔄 Log in again (password changed?)",
            "account_login_library": "📚 Log in to library",
            "account_relogin_library": "🔄 Log in again (password changed?)",
            "account_student_info": "ℹ️ Student info",
            "account_search_book": "🔍 Find a book",
            "account_delete": "🚫 Delete my data",
            "account_delete_confirm": "⚠️ This deletes your saved KLAS and library passwords, your notification history and settings, and your cached student photo. This can't be undone. Delete everything?",
            "account_delete_yes": "🚫 Yes, delete everything",
            "account_delete_no": "↩️ Cancel",
            "enter_book_name": "🔍 What book are you looking for?",
        },
        Language.KO: {
            "test_string": "환영합니다!",
            "welcome": """안녕하세요, {name}님! 👋
광운대학교 학생들을 위한 도우미 봇입니다 🏫
KLAS 과제를 추적하고 마감일 전에 알려드려요 🧭

아래 버튼을 눌러보시거나:
• /account - KLAS와 도서관 계정을 연결하세요
• /show - 마감이 다가오는 일정을 확인하세요
• /menu, /news - 식당 메뉴와 학교 소식

더 많은 기능은 화면 왼쪽 하단의 메뉴 버튼에서 확인하세요.

무언가 작동하지 않으면 @bulochkaskefirochkom 에게 연락주세요 💬 빠르게 도와드릴게요!""",
            "enter_username": "🎓 학번을 입력해주세요 (안전하게 보관됩니다)",
            "enter_password": "🔑 KLAS 비밀번호를 입력해주세요 (암호화됩니다)",
            "enter_phone_number": "📱 전화번호를 입력해주세요",
            "library_enter_username": "🎓 도서관 계정 사용자 이름을 입력해주세요",
            "library_enter_password": "🔑 도서관 계정 비밀번호를 입력해주세요",
            "invalid_credentials": "입력하신 정보가 기록과 일치하지 않네요. 사용자 이름과 비밀번호를 확인하고 /register 로 다시 시도해주세요 🔄",
            "library_login_failed": "입력하신 정보로 도서관 로그인을 할 수 없었어요. 사용자 이름, 비밀번호, 전화번호를 확인하고 /lregister 로 다시 시도해주세요 🔄",
            "library_login_phone_mismatch": "도서관에 등록된 휴대폰 번호로만 로그인할 수 있어요. 전화번호를 다시 확인하고 /lregister 로 다시 시도해주세요 🔄",
            "registration_successful": "🎉 등록 완료! 이미 KLAS 과제를 확인하고 있고, 마감일 전에 알려드릴게요. 처음에는 조용할 수 있는데, 이미 있는 과제를 파악하는 중이라 그런 거니 걱정 마세요.\n\n다음 단계: /account 에서 도서관 계정도 연결하면 QR 출입증과 책 검색을 사용할 수 있어요.",
            "library_registration_successful": "🎉 도서관 연결 완료! /qr로 QR 출입증을 받거나, /account → 🔍 책 찾기로 카탈로그를 검색해보세요.",
            "registration_failed": "등록을 완료할 수 없습니다. 세부 정보를 확인하고 /register로 다시 시도해 주세요 🔄",
            "failed_to_save_credentials": "자격 증명을 저장하는 데 작은 문제가 있었습니다. 나중에 다시 시도해 주세요.",
            "unregistered": "봇에서 성공적으로 등록이 취소되었습니다. 언제든지 다시 등록할 수 있습니다! 👋",
            "register_attempts_limited": "이 학번으로 로그인에 여러 번 실패했어요. KLAS는 비밀번호를 몇 번 틀리면 계정을 잠그기 때문에, 이 학번의 /register 를 1시간 동안 중지합니다. 나중에 다시 시도해주세요 🔒",
            "delete_data_failed": "데이터를 모두 삭제하지 못했어요. /unregister 를 다시 실행해 주세요. 계속 실패하면 봇 관리자에게 알려주세요 ⚠️",
            "need_to_register": "이 기능을 사용하려면 등록이 필요합니다. /register로 시작하세요 - 단 1분이면 됩니다! ✨",
            "no_assignments": "🎉 좋은 소식! 예정된 과제가 없습니다",
            "failed_to_fetch_student_info": "지금은 학생 정보를 가져올 수 없습니다. 나중에 다시 시도해 주세요!",
            "unexpected_error": "이런! 작은 문제가 발생했습니다. 잠시 후 다시 시도해 주세요! 🔄",
            "menu_only_hint": "저는 메뉴 버튼만 이해할 수 있어요. 아래에서 원하는 항목을 선택해 주세요.",
            "callback_error": "버튼을 한 번에 하나씩만 눌러주세요 😊",
            "donate_title": "프로젝트를 커피 한 잔으로 응원해주세요! ☕",
            "donate_description": "여러분의 기부는 이 봇이 계속 운영되고 개선되는 데 도움이 됩니다. 지원해주셔서 감사합니다!",
            "choose_donation_amount": "기부하고 싶은 금액을 선택해주세요:",
            "successful_payment": "✅ 후원해주셔서 감사합니다! 여러분의 기여는 이 봇을 모두에게 더 좋게 만듭니다.",
            "refund_error": "환불을 요청하시려면 결제 메시지에 직접 답장해주세요.",
            "refund_success": "✅ 환불이 성공적으로 처리되었습니다. 봇 지원에 관심을 가져주셔서 감사합니다.",
            "refund_error_message": "지금은 환불을 처리할 수 없었어요. 나중에 다시 시도해주세요.",
            "library_user_not_found": "먼저 도서관 계정을 연결해야 합니다. /lregister 명령어로 시작해보세요.",
            "failed_to_fetch_news": "지금은 최신 뉴스를 불러올 수 없었어요. 나중에 다시 시도해보세요.",
            "choose_news_type": "어떤 종류의 뉴스를 보고 싶으신가요?",
            "please_enter_book_name": "찾으시는 책의 이름을 /search 명령어 뒤에 입력해주세요.",
            "no_books_found": "해당 제목의 책을 찾을 수 없었어요. 철자를 확인하고 다시 시도해보세요! 📚",
            "show_header": "📋 할 일",
            "show_filter_all": "📋 전체",
            "show_filter_lectures": "📚 강의",
            "show_filter_assignments": "📝 과제",
            "show_day_today": "오늘",
            "show_day_tomorrow": "내일",
            "show_day_date": "{month} {day}일 ({weekday}) · {count}일 후",
            "show_later": "🗓 이후 ({count})",
            "show_in_time": "{time_str} 후",
            "weekday_short_0": "월",
            "weekday_short_1": "화",
            "weekday_short_2": "수",
            "weekday_short_3": "목",
            "weekday_short_4": "금",
            "weekday_short_5": "토",
            "weekday_short_6": "일",
            "month_short_1": "1월",
            "month_short_2": "2월",
            "month_short_3": "3월",
            "month_short_4": "4월",
            "month_short_5": "5월",
            "month_short_6": "6월",
            "month_short_7": "7월",
            "month_short_8": "8월",
            "month_short_9": "9월",
            "month_short_10": "10월",
            "month_short_11": "11월",
            "month_short_12": "12월",
            "time_days": "{count}일",
            "time_hours": "{count}시간",
            "time_minutes": "{count}분",
            "too_many_messages": "메시지를 빠르게 보내고 계시네요! 제가 요청을 처리할 수 있도록 잠시만 기다려주세요.",
            "student_info": """📚 학생 프로필:
👨‍🎓 이름: {name} (학번: {uid})
전공: {major}
학년: {grade} | 학기: {semester}
🎯 학점 요약:
• 총 학점: {total_credits}
• 전공 학점: {major_credits_total}
• 교양 학점: {elective_credits_total}
• 평균 점수: {average_score} 📈""",
            "school_food_info": """
🍳 <아침 특가 - 1000원>
복지관 2층에서 오전 8:30 - 9:30에 이용 가능

🍔 <점심 옵션 - 6000원>
복지관 2층에서 오전 11:30 - 오후 2:00에 이용 가능

🍴 <푸드코트 다양한 메뉴 - 8000원>
연구관 지하 1층에서 오전 11:30 - 오후 2:00에 이용 가능

모든 옵션은 뷔페 스타일! 한 번 결제하고 원하는 만큼 즐기세요 🍴
""",
            "school_closed_on_weekend": "대학 식당은 주말에 운영하지 않습니다. 월요일에 다시 뵙겠습니다! 🍽️",
            "foreigners_news": "🌏 국제 뉴스",
            "all_news": "📰 모든 뉴스",
            "tomorrow_menu": "🗓️ 내일 메뉴",
            "info": "ℹ️ 더 알아보기",
            "read_more": "📖 전체 기사 읽기",
            "language_changed": "✅ 완벽해요! 언어 설정이 업데이트되었습니다.",
            "language_change_failed": "언어 설정을 저장하려면 먼저 /register 를 사용해주세요",
            "language_choice": "🌐 원하는 언어를 선택하세요",
            "school_food_menu_header": "🍴 {day} 식사 옵션 🍴\n\n",
            "input_field_placeholder": "메뉴를 선택하세요",
            "button_todos": "📋 할 일",
            "button_qr": "📱 QR",
            "reminder_header": "{emoji} 마감까지 {hours}시간 미만",
            "new_assignments_header": "🆕 KLAS에 새로 등록됨",
            "settings_header": """⚙️ 알림 설정

항목을 누르면 켜고 끌 수 있습니다.""",
            "settings_new_assignments": "🆕 새 과제 알림: {state}",
            "settings_deadline_alerts": "⏰ 마감 알림: {state}",
            "settings_urgent_only": "🎯 긴급 알림만 (6/3/1시간): {state}",
            "settings_quiet_hours": "🌙 방해 금지 {start}:00-{end}:00: {state}",
            "settings_language_button": "🌍 언어",
            "state_on": "켜짐",
            "state_off": "꺼짐",
            "announcement_enable": "✅ 켜기",
            "announcement_dismiss": "🙅 괜찮습니다",
            "announcement_enabled": "✅ 켰습니다. /settings에서 언제든지 변경할 수 있습니다.",
            "announcement_dismissed": "🙅 꺼둔 상태입니다. /settings에서 언제든지 켤 수 있습니다.",
            "credentials_expired": "🔐 저장된 비밀번호로 KLAS에 로그인할 수 없습니다. 비밀번호를 변경하셨다면(광운대는 몇 달마다 변경을 요구합니다) /register로 새 비밀번호를 저장해 주세요. 그때까지 마감 알림이 중지됩니다.",
            "klas_login_required": "🔐 KLAS에 다시 로그인해 주세요. 이제 KLAS는 모든 로그인을 KLAS 앱에서 승인하도록 요구합니다. 아래 버튼을 누른 뒤 휴대폰에서 로그인을 승인해 주세요. 그때까지 마감 알림이 중지됩니다.",
            "klas_push_sent": "📲 휴대폰에서 KLAS 앱을 열고 로그인을 승인해 주세요. 2분 동안 기다릴게요.",
            "klas_login_success": "✅ KLAS에 로그인했습니다. 마감 알림이 다시 켜졌습니다.",
            "klas_push_timeout": "⌛ 시간 안에 로그인이 승인되지 않았습니다. 버튼을 눌러 다시 시도해 주세요.",
            "klas_no_push_device": "📱 KLAS가 KLAS 앱에서 로그인 승인을 요구하지만, 계정에 등록된 휴대폰이 없습니다. KLAS 앱을 설치하고 한 번 로그인한 뒤 버튼을 눌러 주세요.",
            "klas_login_in_progress": "⏳ 이미 KLAS 앱에서 승인을 기다리는 로그인이 있습니다.",
            "klas_login_failed": "😕 지금은 KLAS에 로그인할 수 없습니다. 몇 분 후에 다시 시도해 주세요.",
            "klas_unavailable": "😕 지금 KLAS가 응답하지 않습니다. 몇 분 후에 다시 시도해 주세요.",
            "button_klas_relogin": "🔑 KLAS 로그인",
            "account_header": "👤 내 계정",
            "account_klas_connected": "✅ KLAS: {username}",
            "account_klas_missing": "◻️ KLAS: 연결 안 됨 - 연결하면 과제 확인, 마감 알림, 학생 정보 조회를 사용할 수 있어요",
            "account_library_connected": "✅ 도서관: {username}",
            "account_library_missing": "◻️ 도서관: 연결 안 됨 - 연결하면 QR 출입증 발급과 도서 검색을 사용할 수 있어요",
            "account_login_klas": "💁‍♂️ KLAS 로그인",
            "account_relogin_klas": "🔄 다시 로그인 (비밀번호를 바꾸셨나요?)",
            "account_login_library": "📚 도서관 로그인",
            "account_relogin_library": "🔄 다시 로그인 (비밀번호를 바꾸셨나요?)",
            "account_student_info": "ℹ️ 학생 정보",
            "account_search_book": "🔍 책 찾기",
            "account_delete": "🚫 내 데이터 삭제",
            "account_delete_confirm": "⚠️ KLAS와 도서관 비밀번호, 알림 기록과 설정, 캐시된 학생증 사진이 모두 삭제됩니다. 되돌릴 수 없어요. 모두 삭제할까요?",
            "account_delete_yes": "🚫 네, 모두 삭제",
            "account_delete_no": "↩️ 취소",
            "enter_book_name": "🔍 어떤 책을 찾으시나요?",
        },
        Language.RU: {
            "test_string": "Добро пожаловать!",
            "welcome": """Привет, {name}! 👋
Я бот-помощник для студентов университета Квангвун 🏫
Я отслеживаю задания в KLAS и напоминаю о сроках 🧭

Попробуйте кнопки ниже, или:
• /account - подключите KLAS и библиотеку
• /show - что скоро сдавать
• /menu, /news - меню столовой и новости университета

Больше возможностей - в меню в левом нижнем углу экрана.

Если что-то не работает, напишите мне @bulochkaskefirochkom 💬 Я быстро всё исправлю!
""",
            "enter_username": "🎓 Введите ваш студенческий номер",
            "enter_password": "🔑 Введите ваш пароль от KLAS (будет зашифрован)",
            "enter_phone_number": "📱 Введите ваш номер телефона в формате 01012345678",
            "library_enter_username": "🎓 Введите ваш студенческий номер",
            "library_enter_password": "🔑 Введите пароль от библиотечного аккаунта (обычно ваш день рождения в формате 970326)",
            "invalid_credentials": "Данные не совпадают с данными Университета. Проверьте имя пользователя и пароль, затем попробуйте снова с /register 🔄",
            "library_login_failed": "Не удалось войти в библиотеку с указанными данными. Проверьте имя пользователя, пароль и номер телефона, затем попробуйте снова с /lregister 🔄",
            "library_login_phone_mismatch": "Библиотека принимает вход только с номера телефона, зарегистрированного в вашем библиотечном аккаунте. Проверьте номер телефона и попробуйте снова с /lregister 🔄",
            "registration_successful": "🎉 Готово! Я уже вижу ваши задания в KLAS и буду напоминать о дедлайнах. Первое время может быть тихо - я разбираюсь, что у вас уже есть, это нормально.\n\nДалее: откройте /account, чтобы подключить и библиотеку - для QR-пропуска и поиска книг.",
            "library_registration_successful": "🎉 Библиотека подключена! Получите QR-пропуск через /qr или ищите книги через /account → 🔍 Найти книгу.",
            "registration_failed": "Не удалось завершить регистрацию. Проверьте данные и попробуйте снова с /register 🔄",
            "failed_to_save_credentials": "Возникла небольшая проблема при сохранении данных. Пожалуйста, попробуйте позже.",
            "unregistered": "Вы успешно отменили регистрацию в боте. Вы всегда можете зарегистрироваться снова! 👋",
            "register_attempts_limited": "Слишком много неудачных входов для этого студенческого номера. KLAS блокирует аккаунт после нескольких неверных паролей, поэтому я приостанавливаю /register для него на час. Попробуйте позже 🔒",
            "delete_data_failed": "Не удалось удалить все ваши данные. Выполните /unregister ещё раз и сообщите администратору бота, если ошибка повторяется ⚠️",
            "need_to_register": "Для этой функции требуется регистрация. Начните с /register - это займет всего минуту! ✨",
            "no_assignments": "🎉 Отличные новости! У вас нет предстоящих заданий",
            "failed_to_fetch_student_info": "Не удалось получить информацию о студенте сейчас. Может, попробуем позже?",
            "unexpected_error": "Ой! Возникла небольшая проблема. Попробуйте еще раз через некоторое время! 🔄",
            "menu_only_hint": "Я понимаю только кнопки меню. Пожалуйста, выберите нужный пункт ниже.",
            "callback_error": "Пожалуйста, нажимайте кнопки по одной 😊",
            "donate_title": "Поддержите проект чашкой кофе! ☕",
            "donate_description": "Ваше пожертвование помогает поддерживать работу и улучшать этого бота. Спасибо за вашу поддержку!",
            "choose_donation_amount": "Выберите сумму, которую хотите пожертвовать:",
            "successful_payment": "✅ Спасибо за вашу поддержку! Ваш вклад помогает сделать этого бота лучше для всех.",
            "refund_error": "Для запроса возврата, пожалуйста, ответьте прямо на сообщение о платеже.",
            "refund_success": "✅ Возврат успешно обработан. Спасибо за интерес к поддержке бота.",
            "refund_error_message": "Не удалось обработать возврат сейчас. Пожалуйста, попробуйте позже.",
            "library_user_not_found": "Сначала вам нужно подключить библиотечный аккаунт. Начните с команды /lregister.",
            "failed_to_fetch_news": "Не удалось загрузить свежие новости сейчас. Попробуйте позже.",
            "choose_news_type": "Какой тип новостей вы хотели бы увидеть?",
            "please_enter_book_name": "Пожалуйста, введите название книги, которую вы ищете, после команды /search.",
            "no_books_found": "Не удалось найти книги с таким названием. Проверьте написание и попробуйте снова! 📚",
            "show_header": "📋 Дела",
            "show_filter_all": "📋 Все",
            "show_filter_lectures": "📚 Лекции",
            "show_filter_assignments": "📝 Задания",
            "show_day_today": "Сегодня",
            "show_day_tomorrow": "Завтра",
            "show_day_date": "{weekday}, {day} {month} · через {count} дн.",
            "show_later": "🗓 Позже ({count})",
            "show_in_time": "через {time_str}",
            "weekday_short_0": "Пн",
            "weekday_short_1": "Вт",
            "weekday_short_2": "Ср",
            "weekday_short_3": "Чт",
            "weekday_short_4": "Пт",
            "weekday_short_5": "Сб",
            "weekday_short_6": "Вс",
            "month_short_1": "янв",
            "month_short_2": "фев",
            "month_short_3": "мар",
            "month_short_4": "апр",
            "month_short_5": "мая",
            "month_short_6": "июн",
            "month_short_7": "июл",
            "month_short_8": "авг",
            "month_short_9": "сен",
            "month_short_10": "окт",
            "month_short_11": "ноя",
            "month_short_12": "дек",
            "time_days": "{count}д",
            "time_hours": "{count}ч",
            "time_minutes": "{count}м",
            "too_many_messages": "Вы отправляете сообщения слишком быстро! Пожалуйста, дайте мне немного времени обработать ваши запросы.",
            "student_info": """📚 Профиль студента:
👨‍🎓 Имя: {name} (№: {uid})
Специальность: {major}
Курс: {grade} | Семестр: {semester}
🎯 Сводка по кредитам:
• Всего кредитов: {total_credits}
• Кредиты по специальности: {major_credits_total}
• Кредиты по выборным: {elective_credits_total}
• Средний балл: {average_score} 📈""",
            "school_food_info": """
🍳 <Завтрак - 1000 вон>
Доступно на 2-м этаже 복지관 с 8:30 до 9:30 утра

🍔 <Обед - 6000 вон>
Доступно на 2-м этаже 복지관 с 11:30 до 14:00

🍴 <Обед (фудкорт) - 8000 вон>
Доступно в подвале 연구관 с 11:30 до 14:00

Все варианты в стиле шведского стола! Заплатите один раз и наслаждайтесь сколько хотите 🍴
""",
            "school_closed_on_weekend": "Университетская столовая не работает в выходные. Увидимся в понедельник! 🍽️",
            "foreigners_news": "🌏 Международные новости",
            "all_news": "📰 Все новости",
            "tomorrow_menu": "🗓️ Меню на завтра",
            "info": "ℹ️ Подробнее",
            "read_more": "📖 Читать полную статью",
            "language_changed": "✅ Отлично! Язык успешно обновлен.",
            "language_change_failed": "Чтобы сохранить настройки языка, сначала используйте /register",
            "language_choice": "🌐 Выберите предпочитаемый язык",
            "school_food_menu_header": "🍴 Варианты питания на {day} 🍴\n\n",
            "input_field_placeholder": "Выберите пункт меню...",
            "button_todos": "📋 Дела",
            "button_qr": "📱 QR",
            "reminder_header": "{emoji} Осталось менее {hours} ч",
            "new_assignments_header": "🆕 Новое в KLAS",
            "settings_header": """⚙️ Настройки уведомлений

Нажмите на строку, чтобы переключить.""",
            "settings_new_assignments": "🆕 Новые задания: {state}",
            "settings_deadline_alerts": "⏰ Напоминания о дедлайнах: {state}",
            "settings_urgent_only": "🎯 Только срочные (6/3/1 ч): {state}",
            "settings_quiet_hours": "🌙 Тихие часы {start}:00-{end}:00: {state}",
            "settings_language_button": "🌍 Язык",
            "state_on": "вкл",
            "state_off": "выкл",
            "announcement_enable": "✅ Включить",
            "announcement_dismiss": "🙅 Не надо",
            "announcement_enabled": "✅ Включено. Изменить можно в любой момент через /settings.",
            "announcement_dismissed": "🙅 Оставили выключенным. Включить можно через /settings.",
            "credentials_expired": "🔐 Больше не получается войти в KLAS с сохранённым паролем. Если вы его меняли (KW требует это раз в несколько месяцев), сохраните новый через /register - до этого уведомления о дедлайнах приостановлены.",
            "klas_login_required": "🔐 Нужно снова войти в KLAS. Теперь KLAS требует подтверждать каждый вход в приложении KLAS на телефоне: нажмите кнопку ниже и подтвердите вход на телефоне. До этого уведомления о дедлайнах приостановлены.",
            "klas_push_sent": "📲 Откройте приложение KLAS на телефоне и подтвердите вход. Я подожду 2 минуты.",
            "klas_login_success": "✅ Вход в KLAS выполнен. Уведомления о дедлайнах снова включены.",
            "klas_push_timeout": "⌛ Вход не подтвердили вовремя. Нажмите кнопку, чтобы попробовать снова.",
            "klas_no_push_device": "📱 KLAS просит подтвердить вход в приложении KLAS, но к вашему аккаунту не привязан телефон. Установите приложение KLAS, войдите в нём один раз, затем нажмите кнопку.",
            "klas_login_in_progress": "⏳ Вход уже ждёт подтверждения в приложении KLAS.",
            "klas_login_failed": "😕 Сейчас не получается войти в KLAS. Попробуйте через несколько минут.",
            "klas_unavailable": "😕 KLAS сейчас не отвечает. Попробуйте через несколько минут.",
            "button_klas_relogin": "🔑 Войти в KLAS",
            "account_header": "👤 Ваш аккаунт",
            "account_klas_connected": "✅ KLAS: {username}",
            "account_klas_missing": "◻️ KLAS: не подключён - подключите, чтобы видеть задания, получать напоминания о дедлайнах и проверять данные студента",
            "account_library_connected": "✅ Библиотека: {username}",
            "account_library_missing": "◻️ Библиотека: не подключена - подключите, чтобы получать QR-пропуск и искать книги в каталоге",
            "account_login_klas": "💁‍♂️ Войти в KLAS",
            "account_relogin_klas": "🔄 Войти заново (сменили пароль?)",
            "account_login_library": "📚 Войти в библиотеку",
            "account_relogin_library": "🔄 Войти заново (сменили пароль?)",
            "account_student_info": "ℹ️ Студенческий",
            "account_search_book": "🔍 Найти книгу",
            "account_delete": "🚫 Удалить мои данные",
            "account_delete_confirm": "⚠️ Это удалит сохранённые пароли от KLAS и библиотеки, историю уведомлений и настройки, а также кэш фото студенческого. Отменить будет нельзя. Удалить всё?",
            "account_delete_yes": "🚫 Да, удалить всё",
            "account_delete_no": "↩️ Отмена",
            "enter_book_name": "🔍 Какую книгу ищете?",
        },
    }

    @classmethod
    def get(cls, key: str, lang: Language = Language.EN, **kwargs) -> str:
        """Get string by key and language with optional formatting.

        Never raises: these strings are the bot's only way to talk to the user,
        including from inside handlers' error paths, so a missing key or a
        missing format placeholder must not take the handler down with it.
        """
        for candidate in (lang, Language.EN):
            template = cls._strings.get(candidate, {}).get(key)
            if template is None:
                continue
            try:
                return template.format(**kwargs)
            except (KeyError, IndexError) as e:
                # Template expects a placeholder the caller did not supply
                logging.error(f"Bad format args for string {key!r} ({candidate}): {e}")
                return template

        logging.error(f"Missing string key: {key!r}")
        return key
