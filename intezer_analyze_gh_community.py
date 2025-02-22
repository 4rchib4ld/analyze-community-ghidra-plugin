# Plugin for Intezer Analyze in Ghidra (python 3.8 - Ghidra Bridge)
# @author 4rchib4ld
# @category Analysis
# @keybinding
# @menupath
# @toolbar


import os
import sys


class PluginException(Exception):
    pass


class Proxy:
    def __init__(self, api_key):
        self._api_key = api_key
        self._session = None

    @property
    def session(self):
        if not self._session:
            session = requests.session()
            session.mount("https://", requests.adapters.HTTPAdapter(max_retries=3))
            session.mount("http://", requests.adapters.HTTPAdapter(max_retries=3))
            session.headers = {"User-Agent": "ghidra_plugin/{}".format(VERSION)}
            self._session = session
        return self._session

    def init_access_token(self):
        if "Authorization" not in self.session.headers:
            response = requests.post(
                URLS["get_access_token"], json={"api_key": self._api_key}
            )
            response.raise_for_status()

            token = "Bearer {}".format(response.json()["result"])
            self.session.headers["Authorization"] = token

    def _post(self, url_path, **kwargs):
        self.init_access_token()
        retries = 5
        retries_counter = 0
        while retries_counter <= retries:
            response = self.session.post(url_path, **kwargs)
            if 299 >= response.status_code >= 200 or 499 >= response.status_code >= 400:
                return response
            else:
                time.sleep(2)
                retries_counter += 1

        return None

    def _get(self, url_path, **kwargs):
        self.init_access_token()
        return self.session.get(url_path, **kwargs)

    def create_plugin_report(self, sha256, functions_data):
        response = self._post(
            URLS["create_ghidra_plugin_report"].format(sha256),
            json={"functions_data": functions_data[:FUNCTIONS_LIMIT]},
        )

        if response is None:
            raise Exception("Failed creating plugin report")

        if response.status_code == 404:
            raise PluginException(MESSAGES["file_not_searched"].format(sha256))

        if response.status_code == 409:
            raise PluginException(MESSAGES["not_supported_file"])

        if response.status_code != 201:
            raise Exception(response.reason)

        result_url = response.json()["result_url"]

        return result_url

    def get_plugin_report(self, result_url):
        retries = 5
        retries_counter = 0
        while retries_counter <= retries:
            response = self._get(API_URL + result_url)
            if response.status_code == 202:
                time.sleep(2)
                retries_counter += 1
            else:
                response.raise_for_status()
                return response.json()["result"]


class CodeIntelligenceHelper:
    def __init__(self):
        self._proxy = Proxy(INTEZER_API_KEY)
        self._imagebase = None
        self._entrypoint = None

    @property
    def entrypoint(self):
        if not self._entrypoint:
            self._entrypoint = None
        return self._entrypoint

    @property
    def imagebase(self):
        if not self._imagebase:
            self._imagebase = currentProgram.getImageBase().offset
        return self._imagebase

    def _get_function_map(self, sha256):

        functions_data = []
        function_manager = currentProgram.getFunctionManager()
        functions = function_manager.getFunctions(1)
        image_base = int("0x{}".format(str(currentProgram.imageBase)), 16)

        for f in functions:
            function_start_address = f.getEntryPoint()
            function_end_address = f.getBody().getMaxAddress()

            start_address_as_int = int("0x{}".format(str(function_start_address)), 16)
            end_address_as_int = int("0x{}".format(str(function_end_address)), 16)

            functions_data.append(
                {
                    "start_address": int(start_address_as_int - image_base),
                    "end_address": int(end_address_as_int - image_base + 1),
                }
            )

        is_partial_result = len(functions_data) >= FUNCTIONS_LIMIT

        try:
            result_url = self._proxy.create_plugin_report(sha256, functions_data)
        except requests.ConnectionError:
            # We got connection error when sending a large payload of functions.
            # The fallback is to send a limited amount of functions
            result_url = self._proxy.create_plugin_report(
                sha256, functions_data[:FUNCTIONS_FALLBACK_LIMIT]
            )
            is_partial_result = True

        ghidra_plugin_report = self._proxy.get_plugin_report(result_url)

        if not ghidra_plugin_report["functions"]:
            raise PluginException(MESSAGES["no_genes"])

        functions_map = {}
        for function_address, record in ghidra_plugin_report["functions"].items():
            absolute_address = self._get_absolute_address(int(function_address))
            functions_map[absolute_address] = {"function_address": absolute_address}
            functions_map[absolute_address].update(record)
        return functions_map, is_partial_result

    def _get_absolute_address(self, function_address):
        return hex(self.imagebase + function_address)

    def _enrich_function_map(self, function_map):

        fm = currentProgram.getFunctionManager()
        for function_absolute_address in function_map:
            n = ""  # needed for the cast from string to Address

            n = function_absolute_address.replace("L", "")
            address = currentProgram.getAddressFactory().getAddress(n)
            function_name = fm.getFunctionContaining(address)

            try:
                function_start_address = function_name.getEntryPoint()

                function_map[function_absolute_address][
                    "function_address"
                ] = "0x{}".format(str(function_start_address))
                function_map[function_absolute_address]["function_name"] = str(
                    function_name
                )
            except AttributeError as ex:
                function_map[function_absolute_address][
                    "function_address"
                ] = function_absolute_address
                function_map[function_absolute_address][
                    "function_name"
                ] = ""  # Failed resolve function name

        return function_map

    def write_xml_file(self, functions_map, is_partial_result):
        def prettify(elem):
            """Return a pretty-printed XML string for the Element."""
            rough_string = ElementTree.tostring(elem, "utf-8")
            reparsed = minidom.parseString(rough_string)
            return reparsed.toprettyxml(indent="  ")

        root = Element("Data")

        for key in functions_map.keys():
            entry = SubElement(root, "gene")
            function_address = SubElement(entry, "function_address")
            function_name = SubElement(entry, "function_name")
            software_type = SubElement(entry, "software_type")
            code_reuse = SubElement(entry, "code_reuse")

            try:
                function_address.text = functions_map[key]["function_address"]
            except KeyError as ex:
                print(
                    "Error in key = {0} when getting function_address. meta = ({1})".format(
                        key, functions_map[key]
                    )
                )

            try:
                function_name.text = functions_map[key]["function_name"]
            except KeyError as ex:
                print(
                    "Error in key = {0} when getting function_name. meta = ({1})".format(
                        key, functions_map[key]
                    )
                )

            software_type.text = ",".join(map(str, functions_map[key]["software_type"]))
            for e in functions_map[key]["code_reuse"]:
                code_reuse.text = e

        print(">>>Done building xml. Writing xml...")

        if is_partial_result:
            print(">>>The result is partial due to the large amount of functions")

        output_file = open(PATH_TO_XML, "w")
        output_file.write(prettify(root))
        output_file.close()

    def create_function_map(self, sha256):
        function_map, is_partial_result = self._get_function_map(sha256)
        function_map = self._enrich_function_map(function_map)
        self.write_xml_file(function_map, is_partial_result)


class IntezerAnalyzePlugin:
    def run(self):
        if not INTEZER_API_KEY:
            print(MESSAGES["missing_api_key"])
            return

        path = currentProgram.getExecutablePath()
        program_name = currentProgram.getName()
        creation_date = currentProgram.getCreationDate()
        language_id = currentProgram.getLanguageID()
        compiler_spec_id = currentProgram.getCompilerSpec().getCompilerSpecID()

        if not path:
            print(MESSAGES["file_not_exists"])
            return

        print(
            ">>> Program Info:\n"
            ">>>\t%s:\n"
            "\t%s_%s\n"
            "\t(%s)\n"
            "\t%s" % (program_name, language_id, compiler_spec_id, creation_date, path)
        )

        try:
            with open(path, "rb") as fh:
                sha256 = hashlib.sha256(fh.read()).hexdigest()
        except Exception:
            print(MESSAGES["file_not_exists"])
            return

        print(">>> file SHA : " + sha256)
        print(">>> Start analyzing file...")

        try:
            helper = CodeIntelligenceHelper()

            helper.create_function_map(sha256)
            print(">>> Calling java script")
            runScript("XMLParser.java")

            print(">>> Done analyzing, loading data")
        except Exception:
            traceback.print_exc()


import argparse

if __name__ == "__main__":
    """Ghidra bridge related"""
    in_ghidra = False
    try:
        import ghidra

        # we're in ghidra!
        in_ghidra = True
    except ModuleNotFoundError:
        # not ghidra
        pass

    if in_ghidra:
        import ghidra_bridge_server

        script_file = getSourceFile().getAbsolutePath()
        # spin up a ghidra_bridge_server and spawn the script in external python to connect back to it
        ghidra_bridge_server.GhidraBridgeServer.run_script_across_ghidra_bridge(
            script_file
        )
    else:
        # we're being run outside ghidra! (almost certainly from spawned by run_script_across_ghidra_bridge())
        parser = argparse.ArgumentParser(
            description="Intezer ghidra plugin port to Python3 and ghidra bridge"
        )
        # the script needs to handle these command-line arguments and use them to connect back to the ghidra server that spawned it
        parser.add_argument(
            "--connect_to_host",
            type=str,
            required=False,
            default="127.0.0.1",
            help="IP to connect to the ghidra_bridge server",
        )
        parser.add_argument(
            "--connect_to_port",
            type=int,
            required=False,
            help="Port to connect to the ghidra_bridge server",
            default="13337",
        )
        args = parser.parse_args()
        import hashlib
        import traceback
        import requests
        import time
        from xml.etree import ElementTree
        from xml.etree.ElementTree import Element
        from xml.etree.ElementTree import SubElement
        from xml.dom import minidom
        import ghidra_bridge

        b = ghidra_bridge.GhidraBridge(namespace=globals())
        VERSION = "0.1"
        INTEZER_API_KEY = (
            "API_KEY"  # Can't make it work with Ghidra bridge and env variable
        )
        BASE_URL = os.environ.get("INTEZER_BASE_URL", "https://analyze.intezer.com")
        API_URL = "{}/api".format(BASE_URL)
        DIR = os.getenv(
            "intezer_analyze_ghidra_export_file_path",
            os.path.dirname(os.path.abspath("__file__")),
        )
        PATH_TO_XML = os.path.join(DIR, "items.xml")

        URLS = {
            "get_access_token": "{}/v2-0/get-access-token".format(API_URL),
            "create_ghidra_plugin_report": "{}/v1-2/files/{{}}/community-ida-plugin-report".format(
                API_URL
            ),
        }

        MESSAGES = {
            "missing_api_key": "Please set INTEZER_API_KEY in your environment variables",
            "file_not_open": "Please open a file to analyze",
            "file_not_exists": "Problem occurred while opening file",
            "file_not_searched": "Please analyze the file first on Intezer Analyze. The sha256 is: {}",
            "not_supported_file": "File type not supported for creating Intezer Analyze Ghidra report",
            "authentication_failure": "Failed to authenticate Intezer service",
            "connection_error": "Failed to connect to the Intezer cloud platform",
            "no_genes": "No genes where extracted from the file",
        }

        FUNCTIONS_LIMIT = 10000
        FUNCTIONS_FALLBACK_LIMIT = 1000
        runner = IntezerAnalyzePlugin()
        runner.run()
