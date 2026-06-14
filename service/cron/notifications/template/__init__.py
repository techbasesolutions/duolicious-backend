from urllib.parse import urlencode

from service.config import (
    API_BASE_URL,
    EMAIL_DOMAIN,
)


def big_part(has_intro, has_chat):
    if has_intro and has_chat:
        return 'You have new messages in your chats and intros!'
    if has_intro:
        return 'You have a new message in your intros!'
    if has_chat:
        return 'You have a new message in your chats!'
    return (
        "Our notifier is broken 😵‍💫. Please report this "
        f"to support@{EMAIL_DOMAIN}")

def frequency_url(email, type, frequency):
    base_url = f'{API_BASE_URL}/update-notifications'
    params = {
        'email': email,
        'type': type,
        'frequency': frequency
    }
    encoded_params = urlencode(params)
    return f'{base_url}?{encoded_params}'
