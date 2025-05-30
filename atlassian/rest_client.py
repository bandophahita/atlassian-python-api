# coding=utf-8
import logging
import random
from json import dumps

import requests
from requests.adapters import HTTPAdapter

try:
    from oauthlib.oauth1.rfc5849 import SIGNATURE_RSA_SHA512 as SIGNATURE_RSA
except ImportError:
    from oauthlib.oauth1 import SIGNATURE_RSA
import time

import urllib3
from requests import HTTPError
from requests_oauthlib import OAuth1, OAuth2
from six.moves.urllib.parse import urlencode
from urllib3.util import Retry

from atlassian.request_utils import get_default_logger

log = get_default_logger(__name__)


class AtlassianRestAPI(object):
    default_headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    experimental_headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "X-ExperimentalApi": "opt-in",
    }
    # https://developer.atlassian.com/server/confluence/enable-xsrf-protection-for-your-app/#scripting
    form_token_headers = {
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "X-Atlassian-Token": "no-check",
    }
    # https://developer.atlassian.com/server/confluence/enable-xsrf-protection-for-your-app/#scripting
    no_check_headers = {"X-Atlassian-Token": "no-check"}
    # https://developer.atlassian.com/server/confluence/enable-xsrf-protection-for-your-app/#scripting
    safe_mode_headers = {
        "X-Atlassian-Token": "no-check",
        "Content-Type": "application/vnd.atl.plugins.safe.mode.flag+json",
    }
    # https://developer.atlassian.com/server/confluence/enable-xsrf-protection-for-your-app/#scripting
    experimental_headers_general = {
        "X-Atlassian-Token": "no-check",
        "X-ExperimentalApi": "opt-in",
    }
    response = None

    def __init__(
        self,
        url,
        username=None,
        password=None,
        timeout=75,
        api_root="rest/api",
        api_version="latest",
        verify_ssl=True,
        session=None,
        oauth=None,
        oauth2=None,
        cookies=None,
        advanced_mode=None,
        kerberos=None,
        cloud=False,
        proxies=None,
        token=None,
        cert=None,
        backoff_and_retry=False,
        retry_status_codes=[413, 429, 503],
        max_backoff_seconds=1800,
        max_backoff_retries=1000,
        backoff_factor=1.0,
        backoff_jitter=1.0,
        retry_with_header=True,
    ):
        """
        init function for the AtlassianRestAPI object.

        :param url: The url to be used in the request.
        :param username: Username. Defaults to None.
        :param password: Password. Defaults to None.
        :param timeout: Request timeout. Defaults to 75.
        :param api_root: Root for the api requests. Defaults to "rest/api".
        :param api_version: Version of the API to use. Defaults to "latest".
        :param verify_ssl: Turn on / off SSL verification. Defaults to True.
        :param session: Pass an existing Python requests session object. Defaults to None.
        :param oauth: oauth. Defaults to None.
        :param oauth2: oauth2. Defaults to None.
        :param cookies: Cookies to send with the request. Defaults to None.
        :param advanced_mode: Return results in advanced mode. Defaults to None.
        :param kerberos: Kerberos. Defaults to None.
        :param cloud: Specify if using Atlassian Cloud. Defaults to False.
        :param proxies: Specify proxies to use. Defaults to None.
        :param token: Atlassian / Jira auth token. Defaults to None.
        :param cert: Client-side certificate to use. Defaults to None.
        :param backoff_and_retry: Enable exponential backoff and retry.
                This will retry the request if there is a predefined failure. Primarily
                designed for Atlassian Cloud where API limits are commonly hit if doing
                operations on many issues, and the limits require a cooling off period.
                The wait period before the next request increases exponentially with each
                failed retry. Defaults to False.
        :param retry_status_codes: Errors to match, passed as a list of HTTP
                response codes. Defaults to [413, 429, 503].
        :param max_backoff_seconds: Max backoff seconds. When backing off, requests won't
                wait any longer than this. Defaults to 1800.
        :param max_backoff_retries: Maximum number of retries to try before
                continuing. Defaults to 1000.
        :param backoff_factor: Factor by which to multiply the backoff time (for exponential backoff).
                Defaults to 1.0.
        :param backoff_jitter: Random variation to add to the backoff time to avoid synchronized retries.
                Defaults to 1.0.
        :param retry_with_header: Enable retry logic based on the `Retry-After` header.
                If set to True, the request will automatically retry if the response
                contains a `Retry-After` header with a delay and has a status code of 429.
                The retry delay will be extracted
                from the `Retry-After` header and the request will be paused for the specified
                duration before retrying. Defaults to True.
                If the `Retry-After` header is not present, retries will not occur.
                However, if the `Retry-After` header is missing and `backoff_and_retry` is enabled,
                the retry logic will still be triggered based on the status code 429,
                provided that 429 is included in the `retry_status_codes` list.
        """
        self.url = url
        self.username = username
        self.password = password
        self.timeout = int(timeout)
        self.verify_ssl = verify_ssl
        self.api_root = api_root
        self.api_version = api_version
        self.cookies = cookies
        self.advanced_mode = advanced_mode
        self.cloud = cloud
        self.proxies = proxies
        self.cert = cert
        self.backoff_and_retry = backoff_and_retry
        self.max_backoff_retries = max_backoff_retries
        self.retry_status_codes = retry_status_codes
        self.max_backoff_seconds = max_backoff_seconds
        self.use_urllib3_retry = int(urllib3.__version__.split(".")[0]) >= 2
        self.backoff_factor = backoff_factor
        self.backoff_jitter = backoff_jitter
        self.retry_with_header = retry_with_header
        if session is None:
            self._session = requests.Session()
        else:
            self._session = session

        if proxies is not None:
            self._session.proxies = self.proxies

        if self.backoff_and_retry and self.use_urllib3_retry:
            # Note: we only retry on status and not on any of the
            # other supported reasons
            retries = Retry(
                total=None,
                status=self.max_backoff_retries,
                allowed_methods=None,
                status_forcelist=self.retry_status_codes,
                backoff_factor=self.backoff_factor,
                backoff_jitter=self.backoff_jitter,
                backoff_max=self.max_backoff_seconds,
                respect_retry_after_header=self.retry_with_header,
            )
            self._session.mount(self.url, HTTPAdapter(max_retries=retries))
        if username and password:
            self._create_basic_session(username, password)
        elif token is not None:
            self._create_token_session(token)
        elif oauth is not None:
            self._create_oauth_session(oauth)
        elif oauth2 is not None:
            self._create_oauth2_session(oauth2)
        elif kerberos is not None:
            self._create_kerberos_session(kerberos)
        elif cookies is not None:
            self._session.cookies.update(cookies)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def _create_basic_session(self, username, password):
        self._session.auth = (username, password)

    def _create_token_session(self, token):
        self._update_header("Authorization", f"Bearer {token.strip()}")

    def _create_kerberos_session(self, _):
        from requests_kerberos import OPTIONAL, HTTPKerberosAuth

        self._session.auth = HTTPKerberosAuth(mutual_authentication=OPTIONAL)

    def _create_oauth_session(self, oauth_dict):
        oauth = OAuth1(
            oauth_dict["consumer_key"],
            rsa_key=oauth_dict["key_cert"],
            signature_method=oauth_dict.get("signature_method", SIGNATURE_RSA),
            resource_owner_key=oauth_dict["access_token"],
            resource_owner_secret=oauth_dict["access_token_secret"],
        )
        self._session.auth = oauth

    def _create_oauth2_session(self, oauth_dict):
        """
        Use OAuth 2.0 Authentication
        :param oauth_dict: Dictionary containing access information. Must at
            least contain "client_id" and "token". "token" is a dictionary and
            must at least contain "access_token" and "token_type".
        :return:
        """
        if "client" not in oauth_dict:
            oauth_dict["client"] = None
        oauth = OAuth2(oauth_dict["client_id"], oauth_dict["client"], oauth_dict["token"])
        self._session.auth = oauth

    def _update_header(self, key, value):
        """
        Update header for exist session
        :param key:
        :param value:
        :return:
        """
        self._session.headers.update({key: value})

    @staticmethod
    def _response_handler(response):
        try:
            return response.json()
        except ValueError:
            log.debug("Received response with no content")
            return None
        except Exception as e:
            log.error(e)
            return None

    def _calculate_backoff_value(self, retry_count):
        """
        Calculate the backoff delay for a given retry attempt.

        This method computes an exponential backoff delay based on the retry count and
        a configurable backoff factor. It optionally adds a random jitter to introduce
        variability in the delay, which can help prevent synchronized retries in
        distributed systems. The calculated backoff delay is clamped between 0 and a
        maximum allowable delay (`self.max_backoff_seconds`) to avoid excessively long
        wait times.

        :param retry_count: int, REQUIRED: The current retry attempt number (1-based).
            Determines the exponential backoff delay.
        :return: float: The calculated backoff delay in seconds, adjusted for jitter
            and clamped to the maximum allowable value.
        """
        backoff_value = self.backoff_factor * (2 ** (retry_count - 1))
        if self.backoff_jitter != 0.0:
            backoff_value += random.uniform(0, self.backoff_jitter)  # nosec B311
        return float(max(0, min(self.max_backoff_seconds, backoff_value)))

    def _retry_handler(self):
        """
        Creates and returns a retry handler function for managing HTTP request retries.

        The returned handler function determines whether a request should be retried
        based on the response and retry settings.

        :return: Callable[[Response], bool]: A function that takes an HTTP response object as input and
        returns `True` if the request should be retried, or `False` otherwise.
        """
        retries = 0

        def _handle(response):
            nonlocal retries

            if self.retry_with_header and "Retry-After" in response.headers and response.status_code == 429:
                time.sleep(int(response.headers["Retry-After"]))
                return True

            if not self.backoff_and_retry or self.use_urllib3_retry:
                return False

            if retries < self.max_backoff_retries and response.status_code in self.retry_status_codes:
                retries += 1
                backoff_value = self._calculate_backoff_value(retries)
                time.sleep(backoff_value)
                return True

            return False

        return _handle

    def log_curl_debug(self, method, url, data=None, headers=None, level=logging.DEBUG):
        """

        :param method:
        :param url:
        :param data:
        :param headers:
        :param level:
        :return:
        """
        headers = headers or self.default_headers
        message = "curl --silent -X {method} -H {headers} {data} '{url}'".format(
            method=method,
            headers=" -H ".join([f"'{key}: {value}'" for key, value in list(headers.items())]),
            data="" if not data else f"--data '{dumps(data)}'",
            url=url,
        )
        log.log(level=level, msg=message)

    def resource_url(self, resource, api_root=None, api_version=None):
        if api_root is None:
            api_root = self.api_root
        if api_version is None:
            api_version = self.api_version
        return "/".join(str(s).strip("/") for s in [api_root, api_version, resource] if s is not None)

    @staticmethod
    def url_joiner(url, path, trailing=None):
        url_link = "/".join(str(s).strip("/") for s in [url, path] if s is not None)
        if trailing:
            url_link += "/"
        return url_link

    def close(self):
        return self._session.close()

    def request(
        self,
        method="GET",
        path="/",
        data=None,
        json=None,
        flags=None,
        params=None,
        headers=None,
        files=None,
        trailing=None,
        absolute=False,
        advanced_mode=False,
    ):
        """

        :param method:
        :param path:
        :param data:
        :param json:
        :param flags:
        :param params:
        :param headers:
        :param files:
        :param trailing: bool - OPTIONAL: Add trailing slash to url
        :param absolute: bool, OPTIONAL: Do not prefix url, url is absolute
        :param advanced_mode: bool, OPTIONAL: Return the raw response
        :return:
        """
        url = self.url_joiner(None if absolute else self.url, path, trailing)
        params_already_in_url = True if "?" in url else False
        if params or flags:
            if params_already_in_url:
                url += "&"
            else:
                url += "?"
        if params:
            url += urlencode((params or {}), safe=",")
        if flags:
            url += ("&" if params or params_already_in_url else "") + "&".join(flags or [])
        json_dump = None
        if files is None:
            data = None if not data else dumps(data)
            json_dump = None if not json else dumps(json)

        headers = headers or self.default_headers

        retry_handler = self._retry_handler()
        while True:
            self.log_curl_debug(
                method=method,
                url=url,
                headers=headers,
                data=data or json_dump,
            )
            response = self._session.request(
                method=method,
                url=url,
                headers=headers,
                data=data,
                json=json,
                timeout=self.timeout,
                verify=self.verify_ssl,
                files=files,
                proxies=self.proxies,
                cert=self.cert,
            )
            continue_retries = retry_handler(response)
            if continue_retries:
                continue
            break

        response.encoding = "utf-8"

        log.debug("HTTP: %s %s -> %s %s", method, path, response.status_code, response.reason)
        log.debug("HTTP: Response text -> %s", response.text)

        if self.advanced_mode or advanced_mode:
            return response

        self.raise_for_status(response)
        return response

    def get(
        self,
        path,
        data=None,
        flags=None,
        params=None,
        headers=None,
        not_json_response=None,
        trailing=None,
        absolute=False,
        advanced_mode=False,
    ):
        """
        Get request based on the python-requests module. You can override headers, and also, get not json response
        :param path:
        :param data:
        :param flags:
        :param params:
        :param headers:
        :param not_json_response: OPTIONAL: For get content from raw request's packet
        :param trailing: OPTIONAL: for wrap slash symbol in the end of string
        :param absolute: bool, OPTIONAL: Do not prefix url, url is absolute
        :param advanced_mode: bool, OPTIONAL: Return the raw response
        :return:
        """
        response = self.request(
            "GET",
            path=path,
            flags=flags,
            params=params,
            data=data,
            headers=headers,
            trailing=trailing,
            absolute=absolute,
            advanced_mode=advanced_mode,
        )
        if self.advanced_mode or advanced_mode:
            return response
        if not_json_response:
            return response.content
        else:
            if not response.text:
                return None
            try:
                return response.json()
            except Exception as e:
                log.error(e)
                return response.text

    def _get_response_content(
        self,
        *args,
        fields,
        **kwargs,
    ):
        """
        :param fields: list of tuples in the form (field_name, default value (optional)).
            Used for chaining dictionary value accession.
            E.g. [("field1", "default1"), ("field2", "default2"), ("field3", )]
        """
        response = self.get(*args, **kwargs)
        if "advanced_mode" in kwargs:
            advanced_mode = kwargs["advanced_mode"]
        else:
            advanced_mode = self.advanced_mode

        if not advanced_mode:  # dict
            for field in fields:
                response = response.get(*field)
        else:  # requests.Response
            first_field = fields[0]
            response = response.json().get(*first_field)
            for field in fields[1:]:
                response = response.get(*field)

        return response

    def post(
        self,
        path,
        data=None,
        json=None,
        headers=None,
        files=None,
        params=None,
        trailing=None,
        absolute=False,
        advanced_mode=False,
    ):
        """
        :param path:
        :param data:
        :param json:
        :param headers:
        :param files:
        :param params:
        :param trailing:
        :param absolute:
        :param advanced_mode: bool, OPTIONAL: Return the raw response
        :return: if advanced_mode is not set - returns dictionary. If it is set - returns raw response.
        """
        response = self.request(
            "POST",
            path=path,
            data=data,
            json=json,
            headers=headers,
            files=files,
            params=params,
            trailing=trailing,
            absolute=absolute,
            advanced_mode=advanced_mode,
        )
        if self.advanced_mode or advanced_mode:
            return response
        return self._response_handler(response)

    def put(
        self,
        path,
        data=None,
        headers=None,
        files=None,
        trailing=None,
        params=None,
        absolute=False,
        advanced_mode=False,
    ):
        """
        :param path: Path of request
        :param data:
        :param headers: adjusted headers, usually it's default
        :param files:
        :param trailing:
        :param params:
        :param absolute:
        :param advanced_mode: bool, OPTIONAL: Return the raw response
        :return: if advanced_mode is not set - returns dictionary. If it is set - returns raw response.
        """
        response = self.request(
            "PUT",
            path=path,
            data=data,
            headers=headers,
            files=files,
            params=params,
            trailing=trailing,
            absolute=absolute,
            advanced_mode=advanced_mode,
        )
        if self.advanced_mode or advanced_mode:
            return response
        return self._response_handler(response)

    """
        Partial modification of resource by PATCH Method
        LINK: https://developer.mozilla.org/en-US/docs/Web/HTTP/Methods/PATCH
    """

    def patch(
        self,
        path,
        data=None,
        headers=None,
        files=None,
        trailing=None,
        params=None,
        absolute=False,
        advanced_mode=False,
    ):
        """
        :param path: Path of request
        :param data:
        :param headers: adjusted headers, usually it's default
        :param files:
        :param trailing:
        :param params:
        :param absolute:
        :param advanced_mode: bool, OPTIONAL: Return the raw response
        :return: if advanced_mode is not set - returns dictionary. If it is set - returns raw response.
        """
        response = self.request(
            "PATCH",
            path=path,
            data=data,
            headers=headers,
            files=files,
            params=params,
            trailing=trailing,
            absolute=absolute,
            advanced_mode=advanced_mode,
        )
        if self.advanced_mode or advanced_mode:
            return response
        return self._response_handler(response)

    def delete(
        self,
        path,
        data=None,
        headers=None,
        params=None,
        trailing=None,
        absolute=False,
        advanced_mode=False,
    ):
        """
        Deletes resources at given paths.
        :param path:
        :param data:
        :param headers:
        :param params:
        :param trailing:
        :param absolute:
        :param advanced_mode: bool, OPTIONAL: Return the raw response
        :rtype: dict
        :return: Empty dictionary to have consistent interface.
        Some of Atlassian REST resources don't return any content.
        If advanced_mode is set - returns raw response.
        """
        response = self.request(
            "DELETE",
            path=path,
            data=data,
            headers=headers,
            params=params,
            trailing=trailing,
            absolute=absolute,
            advanced_mode=advanced_mode,
        )
        if self.advanced_mode or advanced_mode:
            return response
        return self._response_handler(response)

    def raise_for_status(self, response):
        """
        Checks the response for errors and throws an exception if return code >= 400
        Since different tools (Atlassian, Jira, ...) have different formats of returned json,
        this method is intended to be overwritten by a tool specific implementation.
        :param response:
        :return:
        """
        if response.status_code == 401 and response.headers.get("Content-Type") != "application/json;charset=UTF-8":
            raise HTTPError("Unauthorized (401)", response=response)

        if 400 <= response.status_code < 600:
            try:
                j = response.json()
                if self.url == "https://api.atlassian.com":
                    error_msg = "\n".join([f"{k}: {v}" for k, v in list(j.items())])
                else:
                    error_msg_list = j.get("errorMessages", list())
                    errors = j.get("errors", dict())
                    if isinstance(errors, dict) and "message" not in errors:
                        error_msg_list.extend(list(errors.values()))
                    elif isinstance(errors, dict) and "message" in errors:
                        error_msg_list.append(errors.get("message", ""))
                    elif isinstance(errors, list):
                        error_msg_list.extend([v.get("message", "") if isinstance(v, dict) else v for v in errors])
                    error_msg = "\n".join(error_msg_list)
            except Exception as e:
                log.error(e)
                response.raise_for_status()
            else:
                raise HTTPError(error_msg, response=response)
        else:
            response.raise_for_status()

    @property
    def session(self):
        """Providing access to the restricted field"""
        return self._session
