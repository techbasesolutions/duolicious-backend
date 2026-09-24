V_AGE = 'Our AI couldn’t verify your age.'
V_EDITED = 'Our AI thinks your image might have been edited.'
V_ETHNCITY = 'Our AI couldn’t verify your ethnicity.'
V_EYEBROW = 'Our AI thinks you’re not touching your eyebrow.'
V_GENDER = 'Our AI couldn’t verify your gender.'
V_MANY_PEOPLE = 'Our AI thinks there’s more than one person in your photo.'
V_NOT_REAL = 'Our AI thinks your image isn’t a real photo.'
V_SCREENSHOT = 'Our AI thinks your image is a screenshot.'
V_NO_PEOPLE = 'Our AI thinks your photo doesn’t have a person in it.'
V_QUEUED = 'Waiting in line for the next selfie checker.'
V_REUSED_SELFIE = 'You can’t submit the same selfie more than once.'
V_SMILING = 'Our AI thinks you’re not smiling.'
V_SOMETHING_WENT_WRONG = 'Something went wrong.'
# Substituted at the API boundary for the three reasons that accuse the
# member of faking the submission. Says what happened and offers the one
# action they have, without naming a cause we cannot evidence. See
# service/person.member_safe_reason.
V_DID_NOT_PASS = 'This check did not pass. You can try again.'
# Written by the cron when a run has died more times than it is allowed to be
# retried. Nothing came back from the classifier at all, so there is no
# reason to name and none is named. It says what happened and offers the one
# action the member has.
V_DID_NOT_FINISH = 'This check did not finish. You can try again.'
V_THUMBS_DOWN = 'Our AI thinks you’re not giving the thumbs down.'
V_UPLOADING_PHOTO = 'Uploading photo.'
