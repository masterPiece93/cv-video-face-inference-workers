"""
GCP Authentication Module

This module handles the authentication responsibility
    of the project .
"""
import os
from typing import Optional, Callable

import google.auth
from google.auth.exceptions import DefaultCredentialsError

def check_gcp_adc_status(logger: Optional[Callable]=None, log_console: bool=False):
    """Checks if Application Default Credentials are available and valid.
    
    args:
        logger (Optional[Callable]) : a callback that does the logging according to you .
        log_console (bool) : if print on stdout/console needed .
    """
    def do_logging(msg: str, level: str = 'info'):
        if logger:
            logger(msg, level=level)
        if log_console:
            print(msg)

    try:
        # Attempt to find the default credentials
        credentials, project = google.auth.default()

        _msg1 = f"""ADC found.
        Project ID: {project}
        """

        do_logging(_msg1)

        # Optional: You can check details about the credentials found
        if hasattr(credentials, "service_account_email"):
            _msg2 = (f"ADC Credential type: Service Account "
                     f"({credentials.service_account_email})")
            do_logging(_msg2)

        elif hasattr(credentials, "quota_project"):
            _msg2 = (f"ADC Credential type: User Account "
                     f"(Quota Project: {credentials.quota_project})")
            do_logging(_msg2)
        else:
            _msg2 = "ADC Credential type: Unknown/Other"
            do_logging(_msg2)

        return True

    except DefaultCredentialsError:
        _msg3 = "ADC is not configured"
        do_logging(_msg3)
        return False
    except Exception as e:
        do_logging(f"An unexpected error occurred while checking ADC: {e}", 'debug')
        return False

if __name__ == '__main__':
    # Run the check
    if (check_gcp_adc_status() or
            'GOOGLE_APPLICATION_CREDENTIALS' in os.environ):
        # You can now confidently use other GCP client libraries
        print("\nProceeding with GCP client library operations.")
    else:
        print("\nPlease run `gcloud auth application-default login` or set "
              "the GOOGLE_APPLICATION_CREDENTIALS environment variable.")
