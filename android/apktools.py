import argparse
import copy
import cloudscraper
import logging
import os
import platform
import random
import re
import shutil
import stat
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from collections import Counter

from bs4 import BeautifulSoup, Tag
from cloudscraper.exceptions import CloudflareChallengeError, CloudflareCaptchaError
from requests.exceptions import ConnectionError, HTTPError, Timeout, TooManyRedirects
from pathlib import Path
from tqdm import tqdm

# Import custom logger
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from logger import setup_logging

# ------------------------------ Parameters Configuration ------------------------------
APP_FILE = "Termius"
TERMIUS_PACKAGE = "com.server.auditor.ssh.client"
DIR_TMP = ".tmp_dir"
EXT_APK = ".apk"
MERGED_APK_FILENAME = f"{APP_FILE}{EXT_APK}"
GOOGLE_PLAY_DOWNLOAD_DIR = "google-play-download"
APK_EDITOR_FILENAME = "APKEditor.jar"
LANGUAGE_XML = "strings.xml"
BASE_URL = "https://www.apkmirror.com"
BASE_APK_URL = f"{BASE_URL}/apk/termius-corporation/termius-ssh-telnet-client/"
GITHUB_REPO_OWNER = "REAndroid"
GITHUB_REPO_NAME = "APKEditor"
APK_SIGN_PROPERTIES = "apk.sign.properties"
ALIGNED_SUFFIX = "_aligned"
SIGNED_SUFFIX = "_signed"
ZH_SUFFIX = "_zh"
TERMIUS_TRANSLATION_SENTINELS = ("connect", "add_host", "all_hosts")

# ------------------------------ Log Configuration ------------------------------
setup_logging(log_level='INFO')
logger = logging.getLogger(__name__)

GLOBAL_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36 Edg/138.0.0.0"
}


def get_scraper():
    """Get CloudScraper instance"""
    return CloudScraperWrapper(
        browser={
            'browser': 'chrome',
            'platform': 'windows',
            'mobile': False
        },
        delay=5,
        timeout=30,
        max_retries=3,
        user_agent=GLOBAL_HEADERS["User-Agent"],
        debug=True
    )


def is_windows():
    """Check if it's a Windows system"""
    return platform.system() == 'Windows'


def get_apksigner_shell():
    """Get apksigner shell command"""
    return "apksigner.bat" if is_windows() else "apksigner"


def split_filename(abs_path):
    """Extract filename from absolute path and separate basename and extension"""
    full_filename = os.path.basename(str(abs_path))
    base_name, ext = os.path.splitext(full_filename)
    return base_name, ext


def run_command(cmd, shell=False, log=True):
    """Execute system command"""
    if log:
        logging.info(f"Executing command: {cmd}")
        if isinstance(cmd, list):
            logging.info(f"Executing command: {' '.join(cmd)}")
        else:
            logging.info(f"Executing command: {cmd}")
    try:
        return subprocess.run(cmd, shell=shell, check=True)
    except subprocess.CalledProcessError as e:
        logging.error(f"Command execution failed: {e}")
        sys.exit(1)
    except Exception as e:
        logging.error(f"Execution error: {e}")
        sys.exit(1)


def replace_file(source_path, target_path):
    """Safely replace target file"""
    if not os.path.exists(source_path):
        logger.error(f"Source file does not exist, cannot replace: {source_path}")
        return False

    target_exists = os.path.exists(target_path)
    if not target_exists:
        logger.warning(f"Target file does not exist, will copy directly: {target_path}")

    try:
        shutil.copy2(source_path, target_path)
        logger.info(f"File replaced successfully: {target_path}, [Source: {source_path}]")
        return True
    except PermissionError:
        logger.error(f"No write permission for target path: {target_path}, check directory permissions")
        return False
    except Exception as e:
        logger.error(f"File replacement failed: {str(e)}")
        return False


def _handle_remove_readonly(func, path, _):
    """Handle read-only files"""
    os.chmod(path, stat.S_IWRITE)
    func(path)


def safe_rmtree(path):
    """Safely delete directory"""
    if not os.path.exists(path):
        return
    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=_handle_remove_readonly)
    else:
        shutil.rmtree(path, onerror=_handle_remove_readonly)


def windows_hide_file(file_path):
    """Hide Windows file"""
    run_command(["attrib", "+h", file_path])


def create_or_recreate_dir(dir_path):
    """Create or recreate directory"""
    if os.path.exists(dir_path):
        if os.path.isdir(dir_path):
            safe_rmtree(dir_path)
        else:
            os.remove(dir_path)
    os.mkdir(dir_path)
    if is_windows():
        windows_hide_file(dir_path)


class CloudScraperWrapper:
    def __init__(self, browser=None, delay=5, timeout=30, max_retries=3, user_agent=None, debug=False):
        self.browser = browser or {
            'browser': 'chrome',
            'platform': 'windows',
            'mobile': False,
            'desktop': True
        }
        self.delay = delay
        self.timeout = timeout
        self.max_retries = max_retries
        self.user_agent = user_agent
        self.debug = debug
        self.scraper = self._create_scraper()

    def _create_scraper(self):
        scraper = cloudscraper.create_scraper(browser=self.browser, delay=self.delay)

        if self.user_agent:
            scraper.headers.update({'User-Agent': self.user_agent})

        return scraper

    def _log(self, message, level="INFO"):
        level = level.lower().strip()
        valid_levels = ['debug', 'info', 'warning', 'error', 'critical']
        if level not in valid_levels:
            raise ValueError(f"无效等级: {level}. 有效值: {valid_levels}")
        log = getattr(logger, level)
        if self.debug:
            log(message)

    def _handle_exception(self, e: Exception, attempt: int) -> bool:
        if isinstance(e, CloudflareCaptchaError):
            self._log(f"Manual captcha required, stop retrying: {str(e)[:200]}", "ERROR")
            return False

        elif isinstance(e, CloudflareChallengeError):
            self._log(f"Cloudflare validation failed (Attempt {attempt + 1}/{self.max_retries}): {str(e)[:200]}", "WARNING")
            return attempt < self.max_retries - 1

        elif isinstance(e, Timeout):
            self._log(f"Request timeout (Attempt {attempt + 1}/{self.max_retries}): {str(e)}", "WARNING")
            return attempt < self.max_retries - 1

        elif isinstance(e, ConnectionError):
            self._log(f"Connection error (Attempt {attempt + 1}/{self.max_retries}): {str(e)}", "WARNING")
            return attempt < self.max_retries - 1

        elif isinstance(e, TooManyRedirects):
            self._log(f"Too many redirects: {str(e)}", "ERROR")
            return False

        elif isinstance(e, HTTPError):
            status = e.response.status_code
            if status in (429, 500, 502, 503, 504):  # Retryable status codes
                self._log(f"HTTP error {status} (Attempt {attempt + 1}/{self.max_retries}): {str(e)}", "WARNING")
                return attempt < self.max_retries - 1
            else:
                self._log(f"HTTP error {status}: {str(e)}", "ERROR")
                return False

        else:
            self._log(f"Unknown error: {type(e).__name__} - {str(e)}", "ERROR")
            return False

    def request(self, method, url, **kwargs):
        last_exception = None

        if self.timeout:
            kwargs.setdefault('timeout', self.timeout)

        for attempt in range(self.max_retries):
            try:
                # Random delay
                time.sleep(random.uniform(0.5, 1.5) * (attempt + 1))

                # Execute request
                self._log(f"Request {method} {url} (Attempt {attempt + 1}/{self.max_retries})")
                response = self.scraper.request(method, url, **kwargs)

                # Check HTTP errors
                response.raise_for_status()

                self._log(f"Successfully received response: {len(response.content)} bytes")
                return response

            except Exception as e:
                last_exception = e
                should_retry = self._handle_exception(e, attempt)

                if not should_retry:
                    break

        # All attempts failed
        error_msg = f"Request failed: {method} {url}"
        if last_exception:
            error_msg += f" - {type(last_exception).__name__}: {str(last_exception)}"
        self._log(error_msg, "CRITICAL")
        raise last_exception if last_exception else Exception("Unknown error")

    def get(self, url, **kwargs):
        return self.request('GET', url, **kwargs)

    def post(self, url, **kwargs):
        return self.request('POST', url, **kwargs)

    def download(self, url, save_path, chunk_size=8192, **kwargs):
        try:
            response = self.get(url, stream=True, **kwargs)
            response.raise_for_status()

            total_size = int(response.headers.get('content-length', 0))
            downloaded = 0

            progress_bar = tqdm(
                total=total_size,
                unit='B',
                unit_scale=True,
                unit_divisor=1024,
                desc=os.path.basename(save_path),
                leave=True
            )

            with open(save_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size):
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)
                        progress_bar.update(len(chunk))

            progress_bar.close()
            self._log(f"File download completed: {save_path} ({downloaded}/{total_size} bytes)" if total_size else
                      f"File download completed: {save_path} (unknown size)")

            return True

        except Exception as e:
            self._log(f"Download failed: {str(e)}", "ERROR")
            return False

    def get_json(self, url, **kwargs):
        response = self.get(url, **kwargs)
        try:
            return response.json()
        except ValueError:
            self._log("Response is not valid JSON format", "ERROR")
            raise


class TermiusAPKModifier:
    """Termius APK Modifier class"""

    def __init__(self, working_dir=None):
        self.working_dir = working_dir or Path(__file__).parent.resolve()
        self._tmp_dir = None
        self.scraper = get_scraper()
        self.sign_properties = self._load_sign_properties()

    @property
    def tmp_dir(self):
        if self._tmp_dir is None:
            self._tmp_dir = self._create_tmp_dir()
        return self._tmp_dir

    @property
    def keystore_dir(self):
        keystore = os.path.join(self.working_dir, "keystore")
        if not os.path.exists(keystore):
            os.mkdir(keystore)
        return keystore

    def _create_tmp_dir(self):
        tmp_dir = os.path.abspath(os.path.join(self.working_dir, DIR_TMP))
        create_or_recreate_dir(tmp_dir)
        return tmp_dir

    def extract_version(self):
        main_page_soup = self._fetch_page(BASE_APK_URL, GLOBAL_HEADERS)

        if not main_page_soup:
            raise Exception("Failed to access main page, terminating program.")

        title_selector = '#primary > div.listWidget.p-relative .appRow h5.appRowTitle'
        title_element = main_page_soup.select_one(title_selector)

        if not title_element:
            logger.error("Application title element not found, please check selector compatibility")
            return None

        full_title = title_element.get_text(strip=True)
        version_match = re.search(r'v?(\d+\.\d+\.\d+)', full_title)

        if not version_match:
            logger.error(f"No valid version found in title: {full_title}")
            return None

        latest_version = version_match.group(1)

        if not latest_version:
            raise Exception("Failed to extract version number, terminating program.")

        logger.info(f"Detected latest version: {latest_version}")
        return latest_version

    def _fetch_page(self, url, headers=None):
        headers = headers or GLOBAL_HEADERS
        try:
            response = self.scraper.get(url, headers=headers)
            return BeautifulSoup(response.text, 'html.parser')
        except Exception as e:
            raise Exception(f"Failed to fetch page: {str(e)}")

    def _build_apkmirror_download_chain(self, base_url, version_slug, headers=None):
        headers = headers or GLOBAL_HEADERS
        try:
            download_page_url = f"{base_url.rstrip('/')}/{version_slug}-release/{version_slug}-android-apk-download/"
            download_soup = self._fetch_page(download_page_url, headers)

            if not download_soup:
                logger.error(f"Download page does not exist or is inaccessible: {download_page_url}")
                return None, None

            apk_button = download_soup.find('a', class_='downloadButton', href=True)
            if not apk_button or not isinstance(apk_button, Tag):
                logger.error("Android APK download button not found, page structure may have changed")
                return None, None

            href = str(apk_button['href'])
            full_apk_url = f"{BASE_URL}{href.rstrip('/')}"
            return download_page_url, full_apk_url

        except Exception as e:
            logger.error(f"Exception occurred while building download chain: {str(e)}")
            return None, None

    def _get_final_download_url(self, url):
        try:
            response = self.scraper.get(url, allow_redirects=True, timeout=15)
            soup = BeautifulSoup(response.text, 'html.parser')
            download_link = soup.find('a', id='download-link', href=True)
            if download_link and isinstance(download_link, Tag):
                return f"{BASE_URL}{download_link['href']}"

            logger.error("Unable to obtain valid download link, page structure may have changed")
            return None

        except Exception as e:
            logger.error(f"Failed to obtain final link: {str(e)}")
            return None

    def _download_apk_editor_jar(self, filename=APK_EDITOR_FILENAME):
        """Download APKEditor.jar"""
        file_path = os.path.join(self.working_dir, filename)
        if os.path.exists(file_path):
            logger.info(f"{filename} already exists, skipping download")
            return

        token = os.environ.get("GH_TOKEN", "").strip()
        if not token:
            raise RuntimeError("GH_TOKEN is required to download APKEditor from the GitHub API")

        try:
            logger.info(f"{filename} not found, starting download...")
            api_url = f"https://api.github.com/repos/{GITHUB_REPO_OWNER}/{GITHUB_REPO_NAME}/releases/latest"
            response = self.scraper.get_json(
                api_url, headers={"Authorization": f"Bearer {token}"}
            )
            assets = response.get('assets', [])

            if not assets:
                raise Exception("No available assets found in latest repository release.")

            download_url = assets[0].get('browser_download_url')
            if not download_url:
                raise Exception("Asset download link not found.")

            logger.info(f"Starting download {filename}: {download_url}")
            if not self.scraper.download(download_url, file_path):
                raise Exception(f"{filename} download failed.")
            logger.info(f"{filename} download completed, saved to: {file_path}")
        except HTTPError as e:
            status = e.response.status_code if e.response is not None else "unknown"
            if status == 403:
                message = e.response.text.lower()
                reason = ("GitHub API rate limit exceeded" if "rate limit" in message
                          else "GitHub API access forbidden; check GH_TOKEN permissions")
            elif status == 429:
                reason = "GitHub API rate limit exceeded"
            else:
                reason = "GitHub API request failed"
            raise RuntimeError(f"Failed to fetch APKEditor release: HTTP {status} ({reason})") from None
        except Exception as e:
            raise RuntimeError(
                f"Error downloading {filename}: {str(e).replace(token, '[REDACTED]')}"
            ) from None

    def _google_play_credentials(self):
        """Load Google Play credentials from the process environment."""
        email = os.environ.get("GOOGLE_PLAY_EMAIL", "").strip()
        aas_token = os.environ.get("GOOGLE_PLAY_AAS_TOKEN", "").strip()
        if not email:
            raise Exception("GOOGLE_PLAY_EMAIL is not configured")
        if not aas_token:
            raise Exception("GOOGLE_PLAY_AAS_TOKEN is not configured")
        return email, aas_token

    def _download_google_play_apk(self):
        """Download the latest Google Play split APK set with apkeep."""
        email, aas_token = self._google_play_credentials()
        download_dir = os.path.join(self.working_dir, GOOGLE_PLAY_DOWNLOAD_DIR)
        create_or_recreate_dir(download_dir)

        logger.info(
            "Downloading latest Termius from Google Play with apkeep "
            f"(package={TERMIUS_PACKAGE}, split_apk=true)"
        )
        run_command([
            "apkeep",
            "-a", TERMIUS_PACKAGE,
            "-d", "google-play",
            "-e", email,
            "-t", aas_token,
            "-o", "split_apk=true,locale=en_US,timezone=UTC",
            download_dir,
        ], log=False)

        apk_files = sorted(Path(download_dir).rglob("*.apk"))
        if not apk_files:
            raise Exception(
                f"apkeep completed but no APK files were found in {download_dir}"
            )

        base_apk_name = f"{TERMIUS_PACKAGE}.apk"
        base_apks = [path for path in apk_files if path.name == base_apk_name]
        if not base_apks:
            raise Exception(
                "Google Play download does not contain the expected base APK "
                f"({base_apk_name}); found: "
                + ", ".join(path.name for path in apk_files)
            )

        input_path = base_apks[0].parent
        logger.info(
            f"Google Play APK input directory: {input_path} "
            f"({len(list(input_path.glob('*.apk')))} APK files)"
        )
        return input_path

    def _download_and_merge_google_play_apk(self):
        """Download Google Play split APKs and merge them into one APK."""
        input_path = self._download_google_play_apk()
        merged_apk = os.path.join(self.tmp_dir, MERGED_APK_FILENAME)
        self._merge_apk_input(input_path, merged_apk)
        metadata = self._extract_apk_metadata(merged_apk)
        self._validate_apk_metadata(metadata)
        logger.info(
            "Google Play APK merged successfully: "
            f"versionName={metadata['version_name']}, "
            f"versionCode={metadata['version_code']}"
        )
        return merged_apk, metadata

    def _extract_apk_metadata(self, apk_file):
        """Read package/version metadata from an APK using aapt."""
        if not os.path.exists(apk_file):
            raise Exception(f"APK file does not exist: {apk_file}")

        logger.info(f"Executing: aapt dump badging {apk_file}")
        try:
            result = subprocess.run(
                ["aapt", "dump", "badging", apk_file],
                check=True,
                capture_output=True,
                text=True,
            )
        except FileNotFoundError as exc:
            raise Exception(
                "aapt was not found; install Android SDK build-tools"
            ) from exc
        except subprocess.CalledProcessError as exc:
            output = (exc.stdout or "") + (exc.stderr or "")
            raise Exception(
                f"aapt dump badging failed for {apk_file}: {output.strip()}"
            ) from exc

        output = result.stdout
        logger.info(output.strip())
        match = re.search(
            r"package: name='([^']+)' versionCode='([^']+)' "
            r"versionName='([^']+)'",
            output,
        )
        if not match:
            raise Exception(
                f"Unable to parse package metadata from aapt output for {apk_file}"
            )

        package_name, version_code, version_name = match.groups()
        return {
            "package_name": package_name,
            "version_code": version_code,
            "version_name": version_name,
        }

    def _validate_apk_metadata(self, metadata):
        """Validate that the downloaded APK is the expected Termius package."""
        if metadata["package_name"] != TERMIUS_PACKAGE:
            raise Exception(
                "Unexpected APK package name: "
                f"{metadata['package_name']} (expected {TERMIUS_PACKAGE})"
            )
        if not metadata["version_name"] or not metadata["version_code"]:
            raise Exception("APK metadata is missing versionName or versionCode")

    def _write_github_output(self, metadata, source):
        """Publish build metadata for GitHub Actions."""
        output_file = os.environ.get("GITHUB_OUTPUT")
        if not output_file:
            return
        with open(output_file, "a", encoding="utf-8") as output:
            output.write(f"version_name={metadata['version_name']}\n")
            output.write(f"version_code={metadata['version_code']}\n")
            output.write(f"source={source}\n")

    def _load_sign_properties(self):
        """Load signing configuration"""
        path_sign_config_file = os.path.join(self.working_dir, APK_SIGN_PROPERTIES)
        if not os.path.exists(path_sign_config_file):
            path_sign_config_file = os.path.abspath(os.path.join(os.path.expanduser('~'), APK_SIGN_PROPERTIES))
            if not os.path.exists(path_sign_config_file):
                return None

        sign_config_file_lines = []
        with open(path_sign_config_file, 'r', encoding='UTF-8') as sign_config_file:
            sign_config_file_lines = sign_config_file.readlines()

        properties = {}
        for line in sign_config_file_lines:
            checked_line = line.strip().replace('\r', '').replace('\n', '')
            if checked_line is None or checked_line == '' or line.startswith('#'):
                continue
            line_parts = checked_line.split('=')
            if len(line_parts) != 2:
                continue
            property_key = line_parts[0].strip()
            property_value = line_parts[1].strip()
            properties[property_key] = property_value

        required_keys = ['sign.keystore', 'sign.keystore.password', 'sign.key.alias', 'sign.key.password']
        if not all(key in properties for key in required_keys):
            return None

        if any(properties[key] == '' for key in ['sign.keystore.password', 'sign.key.alias', 'sign.key.password']):
            return None

        return properties

    def _zipalign_apk(self, apk_filename):
        """Execute APK zipalign operation"""
        logger.info('Executing APK zipalign operation')
        built_apk_file = os.path.join(self.tmp_dir, apk_filename + EXT_APK)
        if not os.path.exists(built_apk_file):
            raise Exception("APK file for zipalign does not exist")

        built_apk_aligned_file = os.path.join(self.tmp_dir, apk_filename + ALIGNED_SUFFIX + EXT_APK)
        if os.path.exists(built_apk_aligned_file):
            os.remove(built_apk_aligned_file)

        run_command(['zipalign', '-p', '-f', '4', built_apk_file, built_apk_aligned_file])
        os.remove(built_apk_file)
        shutil.move(str(built_apk_aligned_file), str(built_apk_file))
        logger.info('Zipalign operation completed')

    def _generate_keystore(self, sign_config):
        """Generate keystore"""
        logger.info('Generating keystore')
        run_command([
            'keytool', '-genkeypair',
            '-alias', sign_config["sign.key.alias"],
            '-keyalg', 'RSA',
            '-keysize', '2048',
            '-validity', '10000',
            '-keystore', os.path.join(self.keystore_dir, sign_config["sign.keystore"]),
            '-storepass', sign_config["sign.keystore.password"],
            '-keypass', sign_config["sign.key.password"],
            '-dname', f"CN={sign_config['sign.key.dname.cn']},C={sign_config['sign.key.dname.c']}"
        ], log=False)
        logger.info('Keystore generation completed')

    def _sign_apk(self, apk_filename):
        """Sign APK file"""
        logger.info('Signing APK file')
        build_apk_file = os.path.join(self.tmp_dir, apk_filename + EXT_APK)
        if not os.path.exists(build_apk_file):
            raise Exception("APK file for signing does not exist")

        if not self.sign_properties:
            raise Exception("Signing properties are not loaded.")

        required_sign_keys = [
            "sign.keystore",
            "sign.keystore.password",
            "sign.key.alias",
            "sign.key.password",
        ]
        missing_keys = [key for key in required_sign_keys if key not in self.sign_properties or self.sign_properties[key] == ""]
        if missing_keys:
            raise Exception(f"Signing configuration missing required keys: {', '.join(missing_keys)}")

        build_apk_signed_file = os.path.join(self.tmp_dir, apk_filename + SIGNED_SUFFIX + EXT_APK)
        if os.path.exists(build_apk_signed_file):
            os.remove(build_apk_signed_file)

        sign_props = self.sign_properties
        run_command([
            get_apksigner_shell(), 'sign',
            '--ks', os.path.join(self.keystore_dir, sign_props["sign.keystore"]),
            '--ks-pass', f"pass:{sign_props['sign.keystore.password']}",
            '--ks-key-alias', sign_props["sign.key.alias"],
            '--key-pass', f"pass:{sign_props['sign.key.password']}",
            '--out', build_apk_signed_file,
            build_apk_file
        ], log=False)

        logger.info('APK signing completed')
        logger.info('Verifying APK signature')
        run_command([get_apksigner_shell(), 'verify', '--verbose', build_apk_signed_file])
        logger.info('APK signature verification completed')

        os.remove(build_apk_file)
        shutil.move(str(build_apk_signed_file), str(build_apk_file))

    def _merge_apk_input(self, input_path, apk_file):
        """Merge a directory or archive of split APKs into one APK."""
        apk_editor_jar = os.path.join(self.working_dir, APK_EDITOR_FILENAME)
        if not os.path.exists(apk_editor_jar):
            raise Exception(f"{apk_editor_jar} not found.")
        if os.path.exists(apk_file):
            os.remove(apk_file)
        run_command([
            "java", "-jar", apk_editor_jar,
            "m", "-i", str(input_path), "-o", apk_file,
        ])

    def _decode_apk(self, apk_file, out_dir):
        """Decompile APK file"""
        apk_editor_jar = os.path.join(self.working_dir, APK_EDITOR_FILENAME)
        if not os.path.exists(apk_editor_jar):
            raise Exception(f"{apk_editor_jar} not found.")
        if os.path.exists(out_dir):
            safe_rmtree(out_dir)
        run_command(['java', '-jar', apk_editor_jar, 'd', '-i', apk_file, '-o', out_dir])

    @staticmethod
    def _xml_parser():
        """Create an XML parser that retains comments from Android resources."""
        return ET.XMLParser(target=ET.TreeBuilder(insert_comments=True))

    @staticmethod
    def _resource_key(element):
        return element.attrib.get("name")

    @staticmethod
    def _resource_text(element):
        """Return text content used when checking Android format placeholders."""
        return "".join(element.itertext())

    @staticmethod
    def _format_placeholders(element):
        """Return Android printf-style placeholders contained in a resource."""
        text = TermiusAPKModifier._resource_text(element)
        # %% is a literal percent sign, not a formatting argument.
        pattern = r"%(?!(?:%))(?:[1-9][0-9]*\$)?[a-zA-Z]"
        return Counter(re.findall(pattern, text))

    @staticmethod
    def _copy_resource_content(source, target):
        """Copy translated XML content while retaining target attributes."""
        target.text = source.text
        target[:] = [copy.deepcopy(child) for child in source]

    def _merge_plural_or_array(self, source, target, resource_name):
        """Merge matching plural quantities or array item positions."""
        if source.tag == "plurals":
            source_children = {
                child.attrib.get("quantity"): child
                for child in source
                if child.tag == "item"
            }
            target_children = {
                child.attrib.get("quantity"): child
                for child in target
                if child.tag == "item"
            }
            child_key = "quantity"
        else:
            source_children = {
                index: child
                for index, child in enumerate(source)
                if child.tag == "item"
            }
            target_children = {
                index: child
                for index, child in enumerate(target)
                if child.tag == "item"
            }
            child_key = "index"

        matched = 0
        for key, target_child in target_children.items():
            source_child = source_children.get(key)
            if source_child is None:
                continue
            if self._format_placeholders(source_child) != self._format_placeholders(target_child):
                logger.warning(
                    "Skipping translation with incompatible format placeholders: "
                    f"{resource_name} ({child_key}={key})"
                )
                continue
            self._copy_resource_content(source_child, target_child)
            matched += 1
        return matched

    def _merge_translation_xml(self, source_xml, default_xml, target_xml):
        """Merge translations using the default resource table as authority."""
        try:
            source_tree = ET.parse(source_xml, parser=self._xml_parser())
            default_tree = ET.parse(default_xml, parser=self._xml_parser())
            target_tree = ET.parse(target_xml, parser=self._xml_parser())
        except ET.ParseError as exc:
            raise Exception(f"Invalid Android resource XML: {exc}") from exc

        source_root = source_tree.getroot()
        default_root = default_tree.getroot()
        target_root = target_tree.getroot()
        supported_tags = {"string", "plurals", "string-array"}

        def resource_map(root):
            return {
                (element.tag, self._resource_key(element)): element
                for element in root
                if element.tag in supported_tags and self._resource_key(element)
            }

        source_resources = resource_map(source_root)
        default_resources = resource_map(default_root)
        target_resources = resource_map(target_root)
        source_names = {
            name for (_, name) in source_resources
        }
        default_names = {
            name for (_, name) in default_resources
        }

        translated_existing = 0
        translated_new = 0
        obsolete_skipped = 0
        format_incompatible = 0
        type_incompatible = 0
        translated_names = set()

        for (resource_type, resource_name), source in source_resources.items():
            default = default_resources.get((resource_type, resource_name))
            if default is None:
                default_same_name = next(
                    (
                        (default_type, default_element)
                        for (default_type, default_name), default_element
                        in default_resources.items()
                        if default_name == resource_name
                    ),
                    None,
                )
                if default_same_name is None:
                    obsolete_skipped += 1
                    logger.warning(
                        "Skipping obsolete translation absent from default resources: "
                        f"{resource_name}"
                    )
                else:
                    type_incompatible += 1
                    logger.warning(
                        "Skipping translation because resource types differ from "
                        f"default resources: {resource_name} "
                        f"({resource_type} vs {default_same_name[0]})"
                    )
                continue

            if self._format_placeholders(source) != self._format_placeholders(default):
                format_incompatible += 1
                logger.warning(
                    "Skipping translation with incompatible format placeholders "
                    f"against default English resource: {resource_name}"
                )
                continue

            target = target_resources.get((resource_type, resource_name))
            if target is not None:
                self._copy_resource_content(source, target)
                translated_existing += 1
            else:
                # The default resource is the validity authority. Add the
                # translated resource to the existing zh-CN document without
                # removing any resources shipped by the APK.
                target = copy.deepcopy(source)
                target_root.append(target)
                target_resources[(resource_type, resource_name)] = target
                translated_new += 1
            translated_names.add(resource_name)

        untranslated_new = len(default_names - source_names)
        if obsolete_skipped:
            logger.warning(
                f"obsolete translations skipped: {obsolete_skipped}"
            )
        if untranslated_new:
            logger.info(
                "untranslated new resources retained via fallback: "
                f"{untranslated_new}"
            )
        if type_incompatible:
            logger.warning(
                f"Resource type incompatible with default resources: {type_incompatible}"
            )
        if format_incompatible:
            logger.warning(
                f"Format-incompatible translation skipped: {format_incompatible}"
            )

        if not translated_names:
            raise Exception(
                "No compatible translations matched the default Android resources"
            )

        # Keep the standard Android resource namespace prefixes stable when present.
        ET.register_namespace("android", "http://schemas.android.com/apk/res/android")
        ET.register_namespace("tools", "http://schemas.android.com/tools")
        ET.register_namespace("xliff", "urn:oasis:names:tc:xliff:document:1.2")
        temporary_xml = f"{target_xml}.tmp"
        try:
            target_tree.write(temporary_xml, encoding="utf-8", xml_declaration=True)
            os.replace(temporary_xml, target_xml)
        finally:
            if os.path.exists(temporary_xml):
                os.remove(temporary_xml)

        self._validate_termius_translations(
            default_xml,
            target_xml,
            translated_names,
        )
        logger.info(
            "Translation merge complete: "
            f"translated existing zh-CN: {translated_existing}, "
            f"translated newly added zh-CN: {translated_new}, "
            f"obsolete translations skipped: {obsolete_skipped}, "
            f"untranslated new resources retained via fallback: {untranslated_new}"
        )
        return {
            "translated_existing": translated_existing,
            "translated_new": translated_new,
            "obsolete_skipped": obsolete_skipped,
            "untranslated_new": untranslated_new,
            "format_incompatible": format_incompatible,
            "type_incompatible": type_incompatible,
        }

    def _validate_termius_translations(
        self, default_xml, target_xml, translated_names
    ):
        """Ensure core Termius UI resources were actually localized."""
        try:
            default_root = ET.parse(
                default_xml, parser=self._xml_parser()
            ).getroot()
            target_root = ET.parse(
                target_xml, parser=self._xml_parser()
            ).getroot()
        except ET.ParseError as exc:
            raise Exception(f"Invalid XML during localization validation: {exc}") from exc

        default_strings = {
            self._resource_key(element): element
            for element in default_root
            if element.tag == "string" and self._resource_key(element)
        }
        target_strings = {
            self._resource_key(element): element
            for element in target_root
            if element.tag == "string" and self._resource_key(element)
        }
        default_missing = []
        missing = []
        unchanged = []
        for resource_name in TERMIUS_TRANSLATION_SENTINELS:
            default = default_strings.get(resource_name)
            target = target_strings.get(resource_name)
            if default is None:
                default_missing.append(resource_name)
                logger.warning(
                    f"Termius validation resource is absent from default APK: {resource_name}"
                )
                continue
            if target is None or resource_name not in translated_names:
                missing.append(resource_name)
                continue
            target_text = self._resource_text(target).strip()
            default_text = self._resource_text(default).strip()
            if not target_text or target_text == default_text:
                unchanged.append(resource_name)

        if default_missing or missing or unchanged:
            raise Exception(
                "Termius-specific localization validation failed: "
                f"default_missing={default_missing}, "
                f"missing={missing}, unchanged={unchanged}"
            )
        logger.info(
            "Termius-specific localization validation passed: "
            + ", ".join(TERMIUS_TRANSLATION_SENTINELS)
        )

    def _replace_language_xml(self, target_dir):
        """Merge repository translations into decoded APK resources."""
        src_xml = os.path.join(self.working_dir, LANGUAGE_XML)
        if not os.path.exists(src_xml):
            raise Exception(f"Language source file not found: {src_xml}")

        zh_candidates = sorted(
            Path(target_dir).glob(
                "resources/*/res/values-zh-rCN/strings.xml"
            )
        )
        if not zh_candidates:
            raise Exception(
                "Target Chinese strings.xml was not found under the decoded APK "
                "(resources/*/res/values-zh-rCN/strings.xml)"
            )

        package_1_candidates = [
            path for path in zh_candidates if path.parts[-4] == "package_1"
        ]
        target_xml = package_1_candidates[0] if package_1_candidates else zh_candidates[0]
        resource_root = target_xml.parent.parent
        default_xml = resource_root / "values" / "strings.xml"
        if not default_xml.exists():
            default_candidates = sorted(
                Path(target_dir).glob(
                    f"resources/{target_xml.parts[-4]}/res/values/strings.xml"
                )
            )
            if default_candidates:
                default_xml = default_candidates[0]
        if not default_xml.exists():
            raise Exception(
                "Default values/strings.xml was not found for the selected APK resource package: "
                f"{resource_root}"
            )

        if len(zh_candidates) > 1:
            logger.warning(
                "Found multiple Chinese strings.xml files; using "
                f"{target_xml}"
            )

        logger.info(
            "Merging translations using default resources as authority: "
            f"Source={src_xml}, Default={default_xml}, Target={target_xml}"
        )
        self._merge_translation_xml(src_xml, str(default_xml), str(target_xml))

    def _verify_final_apk(self, apk_file, expected_metadata):
        """Run final APK metadata and signature verification."""
        logger.info("Final APK metadata (aapt dump badging):")
        metadata = self._extract_apk_metadata(apk_file)
        self._validate_apk_metadata(metadata)
        if (
            metadata["version_name"] != expected_metadata["version_name"]
            or metadata["version_code"] != expected_metadata["version_code"]
        ):
            raise Exception(
                "Final APK version changed unexpectedly: "
                f"{metadata['version_name']} ({metadata['version_code']}) vs "
                f"{expected_metadata['version_name']} "
                f"({expected_metadata['version_code']})"
            )
        logger.info(
            f"package={metadata['package_name']} "
            f"versionName={metadata['version_name']} "
            f"versionCode={metadata['version_code']}"
        )
        logger.info("Final APK signature verification (apksigner verify --verbose):")
        run_command([get_apksigner_shell(), "verify", "--verbose", apk_file])

    def _build_apk(self, out_dir, apk_filename):
        """Repackage APK file"""
        apk_editor_jar = os.path.join(self.working_dir, APK_EDITOR_FILENAME)
        if not os.path.exists(apk_editor_jar):
            raise Exception(f"{apk_editor_jar} not found.")
        if not os.path.exists(out_dir):
            raise Exception("Decompile directory not found.")
        apk_file = os.path.join(self.tmp_dir, apk_filename + EXT_APK)
        if os.path.exists(apk_file):
            os.remove(apk_file)
        run_command(['java', '-jar', apk_editor_jar, 'b', '-i', out_dir, '-o', apk_file])

    def _export_apk(self, apk_filename, export_filename):
        """Export final APK file"""
        apk_file = os.path.join(self.tmp_dir, apk_filename + EXT_APK)
        out_dir = os.path.join(self.working_dir, "out")
        if not os.path.exists(out_dir):
            os.mkdir(out_dir)
        export_apk_file = os.path.join(out_dir, export_filename + EXT_APK)
        if os.path.exists(export_apk_file):
            os.remove(export_apk_file)
        shutil.move(str(apk_file), str(export_apk_file))

    def _check_required_files(self):
        """Check signing/localization inputs and acquire the Google Play APK."""
        if not self.sign_properties:
            raise Exception("Signing configuration file not found.")

        language_xml = os.path.join(self.working_dir, LANGUAGE_XML)
        if not os.path.exists(language_xml):
            raise Exception("Language xml not found.")

        apk_editor_jar = os.path.join(self.working_dir, APK_EDITOR_FILENAME)
        if not os.path.exists(apk_editor_jar):
            self._download_apk_editor_jar(APK_EDITOR_FILENAME)

        sign_keystore = os.path.join(
            self.keystore_dir,
            self.sign_properties["sign.keystore"],
        )
        if not os.path.exists(sign_keystore):
            self._generate_keystore(self.sign_properties)

        return self._download_and_merge_google_play_apk()

    def modify_apk(self, before_build=None):
        """Download, localize, repackage, align, and sign the APK."""
        try:
            logger.info("Starting APK file processing")
            merged_apk, metadata = self._check_required_files()
            if before_build is not None and not before_build(metadata):
                safe_rmtree(self.tmp_dir)
                return None
            source = "google-play"

            decompile_dir = os.path.join(self.tmp_dir, APP_FILE)
            filename_zh = APP_FILE + ZH_SUFFIX

            logger.info("Decompiling merged Google Play APK")
            self._decode_apk(merged_apk, decompile_dir)

            logger.info("Replacing language resources")
            self._replace_language_xml(decompile_dir)

            logger.info("Repackaging APK file")
            self._build_apk(decompile_dir, filename_zh)

            logger.info("Executing zipalign operation")
            self._zipalign_apk(filename_zh)

            logger.info("Signing APK file")
            self._sign_apk(filename_zh)

            logger.info("Exporting final APK file")
            self._export_apk(filename_zh, APP_FILE)
            final_apk = os.path.join(self.working_dir, "out", APP_FILE + EXT_APK)

            self._verify_final_apk(final_apk, metadata)
            self._write_github_output(metadata, source)

            logger.info(f"Cleaning temporary directory: {self.tmp_dir}")
            safe_rmtree(self.tmp_dir)

            logger.info(
                "APK modification completed: "
                f"source={source}, version_name={metadata['version_name']}, "
                f"version_code={metadata['version_code']}"
            )
            return metadata

        except Exception as e:
            logger.error(f"Process terminated abnormally: {e}")
            sys.exit(1)


def main():
    """Main function"""
    logger.info("Process initialization started")
    parser = argparse.ArgumentParser(
        description="🔧 Termius APK Localization Modification Tool",
        epilog="📝 Usage examples:\n  python apktools.py -l  # Execute localization modification\n  python apktools.py -v  # Display version information",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )

    parser.add_argument(
        "-l", "--localize",
        action="store_true",
        help="🌍 Enable localization patch (Chinese translation)"
    )

    parser.add_argument(
        "-v", "--version",
        action="store_true",
        help="📌 Display program version information"
    )

    args = parser.parse_args()

    modifier = TermiusAPKModifier()
    if args.version:
        logger.error("--version is no longer a network-free operation; run the full build instead.")
        sys.exit(2)

    if not args.localize and not args.version:
        logger.info("No parameters specified, will execute default localization operation")
        args.localize = True

    if args.localize:
        modifier.modify_apk()

    logger.info("Process completed")


if __name__ == "__main__":
    main()
