import base64
import copy
import getpass
import glob
import json
import os
import random
import re
import string
import sys
import tempfile
import time

import requests

start = time.time()

URL_DATACENTERS = "https://api.ionos.com/cloudapi/v6/datacenters"


def name_to_href(name, api_url, auth_headers):
    """Runs through all items to get a server's or component's unique href.

    Args:
        name: Either a server's or a component's name.
        api_url: Datacenter URL (in case of server) or server URL (otherwise).
        auth_headers:

    Returns:
        Returns unique href of server or component.

    Raises:
        exit(1) if server or component does not exist.
    """
    items = json.loads(requests.get(api_url, headers=auth_headers).text)["items"]
    for item in items:
        href = json.loads(requests.get(item["href"], headers=auth_headers).text)
        if href["properties"]["name"] == name:
            return href["href"]
    print(f"??? {name} does not exist?")
    exit(1)


def json_file_open(file_name):
    """Reads a single json file and returns its contents.

    Args:
        file_name: Name (full path) of the file to open.

    Returns:
        Returns file's contents as JSON dictionary.
    """
    json_obj = {}
    with open(file_name) as json_file:
        text = json_file.read()
        json_obj = json.loads(text)
        if "LOCATION" in os.environ:
            with open(
                "/datacenters/"
                + os.environ.get("DATACENTER")
                + "/."
                + os.environ.get("LOCATION")
                + ".json"
            ) as config_file:
                for k, v in json.loads(config_file.read()).items():
                    json_obj = json.loads(
                        json.dumps(json_obj).replace("{{ " + k + " }}", v)
                    )
    return json_obj


def globbing(path):
    """Reads a list of (*.json) files and returns its contents.

    Args:
        path: *.json path of the datacenter config files.

    Returns:
        Returns a dict containing the contents of the datacenter config files.
    """
    files = {}
    for glob_file in glob.glob(path):
        ret = json_file_open(glob_file)
        files[os.path.basename(glob_file)] = ret
    return files


def header_function(username, password, contract_nr):
    """Creates the IONOS API's `auth_headers`, needed by all types of requests.

    Args:
        username: Email address.
        password: Password.
        contract_nr: Contract number.

    Returns:
        Returns the `auth_headers` for the given user+password and contract number.
    """
    base_up = username.rstrip() + ":" + password.rstrip()
    base_up_en = base_up.encode("ascii")
    auth_base_64_en = base64.b64encode(base_up_en)
    auth_base_64_de = auth_base_64_en.decode("ascii")
    headers = {
        "headers": {
            "X-Contract-Number": contract_nr.rstrip(),
            "Authorization": "Basic " + auth_base_64_de,
        }
    }
    # print(headers)
    return headers


def all_available(hrefs, auth_headers):
    """Blocks until a (set of) server(s) is available.

    Args:
        hrefs: List of server hrefs to wait for availability.
        auth_headers:

    Raises:
        exit(1) if the limit of requests (timeout) is reached.
    """
    all_available = False
    status_finished = "AVAILABLE"
    limit = 120
    while not all_available:
        all_available = True
        if limit == 0:
            print("!!! Limit reached!", flush=True)
            exit(1)
        print("... Server BUSY...", flush=True)
        time.sleep(5)
        for href in hrefs:
            try:
                status = json.loads(requests.get(href, headers=auth_headers).text)[
                    "metadata"
                ]["state"]
            except KeyError:
                status = "BUSY"
            if status != status_finished:
                all_available = False
                break
        limit -= 1


def server_exists(server_name, api_url, auth_headers):
    """Checks if a server is already existing in the datacenter.

    Args:
        server_name: Name of the server.
        api_url: Unique URL (href) of the server.
        auth_headers:

    Returns:
        Returns True if server exists. Returns False if not.
    """
    items = json.loads(requests.get(api_url, headers=auth_headers).text)["items"]
    for item in items:
        server = json.loads(requests.get(item["href"], headers=auth_headers).text)
        if server["properties"]["name"] == server_name:
            print(f"... Server {server_name} exists.")
            return True
    print(f"... Server {server_name} does not exist.")
    return False


def user_data(dir, file_name):
    """Generates cloud-config userData from template.

    Args:
        dir: Directory containing cloud-configs (templates).
        file_name: Name of the cloud-config file to be parsed.

    Returns:
        Base64 encoded cloud-config userData.
    """
    # creating a temporary file because docker container has a read-only filesystem:
    temp_braces = tempfile.TemporaryFile(mode="r+")
    with open(f"{dir}{file_name}") as fp:
        # Note that `<dir>` is supposed to end with a `/`.
        braces = fp.readlines()
        for line in braces:
            if line.startswith("{{"):
                includes_dir = (
                    "/datacenters/" + os.environ.get("DATACENTER") + "/includes/"
                )
                includes_file = line[2:].split("}}")[0].strip()
                if not os.path.isfile(includes_dir + includes_file):
                    includes_dir = "/datacenters/includes/"
                with open(includes_dir + includes_file) as fp:
                    temp_braces.write(fp.read())
            else:
                temp_braces.write(line)
    temp_braces.seek(0)
    server_yaml = temp_braces.read()
    message_bytes = server_yaml.encode("ascii")
    base64_bytes = base64.b64encode(message_bytes)
    base64_message = base64_bytes.decode("ascii")
    return base64_message


def create_single(server_name, json_files, api_url, auth_headers):
    """Creates a single server of the datacenter.

    Args:
        server_name: Name of the server to create.
        json_files: Contains all server specifications.
        api_url: name_to_href(...) + "/servers" a.k.a. URL_SERVERS.
        auth_headers:

    Returns:
        Returns the href (unique server id) of the server created.
    """
    print(f"+++ Creating server: {server_name}")
    server = json_files[server_name + ".json"]["server"]

    # "firewallrules" is an unrecognized field, therefore:
    # deepcopy the server's JSON and temporarily delete the "firewallrules"
    # - so that the original json_files stays intact for later usage...
    server_deepcopy = copy.deepcopy(server)
    for item in server_deepcopy["entities"]["nics"]["items"]:
        if "firewallrules" in item:
            del item["firewallrules"]

    # "imagePassword" MUST NOT be empty for IONOS public images.
    # Generate random password if "imagePassword" is null in JSON config,
    # but do not print it, effectively prohibiting 'root' user log in.
    try:
        for item in server_deepcopy["entities"]["volumes"]["items"]:
            if item["properties"]["imagePassword"] is None:
                item["properties"]["imagePassword"] = "".join(
                    random.choices(
                        string.ascii_uppercase + string.ascii_lowercase + string.digits,
                        k=32,
                    )
                )
    except KeyError:
        pass

    dir = "/datacenters/" + os.environ.get("DATACENTER") + "/cloud-configs/"
    for item in server_deepcopy["entities"]["volumes"]["items"]:
        name = item["properties"]["name"]
        file_name = f"{name}.yaml"
        if file_name in os.listdir(dir) and "-boot" in name:
            item["properties"]["userData"] = user_data(dir, file_name)
            # Note that `<name>` is supposed to be `<server_name>-boot`.

    res = json.loads(
        requests.post(api_url, json=server_deepcopy, headers=auth_headers).text
    )
    print(res)
    href = res["href"]
    all_available([href], auth_headers)
    return href


def attach(type, json_files, api_url, auth_headers):
    """Creates and attaches all components of <type> to an existing server.

    Args:
        type: Either "volumes" or "nics".
        json_files: Contains all server specifications.
        api_url: Unique URL (href) of the server.
        auth_headers:
    """
    server = json.loads(requests.get(api_url, headers=auth_headers).text)
    server_name = server["properties"]["name"]
    for t in json_files[server_name + ".json"][type]:
        attach_single(
            api_url,
            server_name,
            t["properties"]["name"],
            type,
            json_files,
            auth_headers,
        )


def attach_single(href, server_name, name, type, json_files, auth_headers):
    """Creates and attaches one component of <type> to an existing server.

    Args:
        href: Unique URL of a server.
        server_name: Name of the server.
        name: Name of the <type>.
        type: Either "volumes" or "nics".
        json_files: Contains all server specifications.
        auth_headers:

    Raises:
        exit(1) if the server does not exist or the component's name is wrong.
    """
    URL_SERVERS = (
        name_to_href(os.environ.get("DATACENTER"), URL_DATACENTERS, auth_headers)
        + "/servers"
    )
    if not server_exists(server_name, URL_SERVERS, auth_headers):
        exit(1)
    json_file = json_files[server_name + ".json"]
    components = [t["properties"]["name"] for t in json_file[type]]
    if name not in components:
        print(f"??? {name} not in {components}?")
        exit(1)
    url_type = href + "/" + type
    idx = components.index(name)

    # "imagePassword" MUST NOT be empty for IONOS public images.
    # Generate random password if "imagePassword" is null in JSON config,
    # but do not print it, effectively prohibiting 'root' user log in.
    try:
        if type == "volumes":
            if json_file[type][idx]["properties"]["imagePassword"] is None:
                json_file[type][idx]["properties"]["imagePassword"] = "".join(
                    random.choices(
                        string.ascii_uppercase + string.ascii_lowercase + string.digits,
                        k=32,
                    )
                )
    except KeyError:
        pass

    dir = "/datacenters/" + os.environ.get("DATACENTER") + "/cloud-configs/"
    file_name = f"{server_name}-boot.yaml"
    if type == "volumes" and file_name in os.listdir(dir) and "-boot" in name:
        json_file[type][idx]["properties"]["userData"] = user_data(dir, file_name)
    print("+++ Attaching " + type + "." + name + " to " + server_name + ".")
    component = json.loads(
        requests.post(
            url_type,
            json={"properties": json_file[type][idx]["properties"]},
            headers=auth_headers,
        ).text
    )

    all_available([href], auth_headers)
    # Make newly attached volume a boot device if name ends with "-boot":
    if type == "volumes" and component["properties"]["name"].endswith("-boot"):
        requests.patch(
            href, json={"bootVolume": {"id": component["id"]}}, headers=auth_headers
        )
    all_available([href], auth_headers)


def create(server_name, json_files, api_url, auth_headers):
    """Creates a server with all components, by calling create_single()/attach().

    Args:
        server_name: Name of the server.
        json_files: Contains all server specifications.
        api_url: name_to_href(...) + "/servers" a.k.a. URL_SERVERS.
        auth_headers:

    Raises:
        exit(1) if the server already exists.
    """
    if server_exists(server_name, api_url, auth_headers):
        exit(1)
    href = create_single(server_name, json_files, api_url, auth_headers)
    attach("volumes", json_files, href, auth_headers)
    attach("nics", json_files, href, auth_headers)
    fwrules_create(server_name, json_files, auth_headers)


def delete_single(server_object, auth_headers):
    """Deletes a single server from the datacenter.

    Args:
        server_object: JSON server object with all information, e.g., name and href.
        auth_headers:
    """
    print("--- Deleting server: " + server_object["properties"]["name"])
    requests.delete(server_object["href"], headers=auth_headers)


def detach(type, json_files, api_url, auth_headers):
    """Detaches and deletes all components of <type> from an existing server.

    Args:
        type: Either "volumes" or "nics".
        json_files: Contains all server specifications.
        api_url: Unique URL (href) of the server.
        auth_headers:
    """
    server = json.loads(requests.get(api_url, headers=auth_headers).text)
    server_name = server["properties"]["name"]
    url_type = api_url + "/" + type
    items = json.loads(requests.get(url_type, headers=auth_headers).text)["items"]
    for item in items:
        item_href = item["href"]
        item_name = json.loads(requests.get(item_href, headers=auth_headers).text)[
            "properties"
        ]["name"]
        detach_single(item_href, server_name, item_name, type, json_files, auth_headers)


def detach_single(href, server_name, name, type, json_files, auth_headers):
    """Detaches and deletes one component of <type> from an existing server.

    Args:
        href: Unique URL of a server.
        server_name: Name of the server.
        name: Name of the <type>.
        type: Either "volumes" or "nics".
        json_files: Contains all server specifications.
        auth_headers:

    Raises:
        exit(1) if the server does not exist or the component's name is wrong.
    """
    URL_SERVERS = (
        name_to_href(os.environ.get("DATACENTER"), URL_DATACENTERS, auth_headers)
        + "/servers"
    )
    if not server_exists(server_name, URL_SERVERS, auth_headers):
        exit(1)
    json_file = json_files[server_name + ".json"]
    components = [
        t["properties"]["name"]
        for t in json_file[type] + json_file["server"]["entities"][type]["items"]
    ]
    if name not in components:
        print(f"??? {name} not in {components}?")
        exit(1)
    print("--- Detaching " + type + "." + name + " from " + server_name + ".")
    requests.delete(href, headers=auth_headers)
    server_href = name_to_href(server_name, URL_SERVERS, auth_headers)
    all_available([server_href], auth_headers)


def delete(server_name, json_files, api_url, auth_headers):
    """Deletes a server with all components, by calling detach()/delete_single().

    Args:
        server_name: Name of the server.
        json_files: Contains all server specifications.
        api_url: name_to_href(...) + "/servers" a.k.a. URL_SERVERS.
        auth_headers:

    Raises:
        exit(1) if the server does not already exist.
    """
    if not server_exists(server_name, api_url, auth_headers):
        exit(1)
    server_href = name_to_href(server_name, api_url, auth_headers)
    server_object = json.loads(requests.get(server_href, headers=auth_headers).text)
    all_available([server_href], auth_headers)
    detach("nics", json_files, server_href, auth_headers)
    detach("volumes", json_files, server_href, auth_headers)
    delete_single(server_object, auth_headers)


def fwrules_create_single(name, server_name, nic_name, json_files, auth_headers):
    """Creates a single firewall rule on a NIC of a server.

    Args:
        name: Name of the firewall rule.
        server_name: Name of the server.
        nic_name: Name of the NIC.
        json_files: Contains all server specifications.
        auth_headers:

    Raises:
        exit(1) if the server does not exist or the rule's name is wrong.
    """
    URL_SERVERS = (
        name_to_href(os.environ.get("DATACENTER"), URL_DATACENTERS, auth_headers)
        + "/servers"
    )
    if not server_exists(server_name, URL_SERVERS, auth_headers):
        exit(1)
    nics = (
        json_files[server_name + ".json"]["nics"]
        + json_files[server_name + ".json"]["server"]["entities"]["nics"]["items"]
    )
    for nic in nics:
        if nic_name == nic["properties"]["name"]:
            rules = nic["firewallrules"]
            rules_names = [rule["properties"]["name"] for rule in rules]
            rules_set = set(rules_names)
            if len(rules_set) != len(rules):
                print(f"Duplicate firewallrules name detected for nic.{nic_name}:")
                print(f'!!! nic["firewallrules"] = {rules_names}')
                print("Ensure that firewallrules names are unique per NIC.")
                exit(1)
            for rule in rules:
                if rule["properties"]["name"] == name:
                    nic_href = (
                        name_to_href(server_name, URL_SERVERS, auth_headers) + "/nics"
                    )
                    fw_href = (
                        name_to_href(nic_name, nic_href, auth_headers)
                        + "/firewallrules"
                    )
                    print(
                        f"+++ Creating firewallrules.{name} "
                        f"on nics.{nic_name} of server.{server_name}."
                    )
                    requests.post(fw_href, json=rule, headers=auth_headers)
                    server_href = name_to_href(server_name, URL_SERVERS, auth_headers)
                    all_available([server_href], auth_headers)


def fwrules_create(server_name, json_files, auth_headers):
    """Creates all firewall rules on all NICs of a server.

    Args:
        server_name: Name of the server.
        json_files: Contains all server specifications.
        auth_headers:
    """
    nics = (
        json_files[server_name + ".json"]["nics"]
        + json_files[server_name + ".json"]["server"]["entities"]["nics"]["items"]
    )
    for nic in nics:
        if "firewallrules" in nic:
            for firewallrule in nic["firewallrules"]:
                fwrules_create_single(
                    firewallrule["properties"]["name"],
                    server_name,
                    nic["properties"]["name"],
                    json_files,
                    auth_headers,
                )


def fwrules_delete_single(api_url, server_name, nic_name, name, auth_headers):
    """Deletes a single firewall rule from a NIC of a server.

    Args:
        api_url: name_to_href(...) + "/servers" a.k.a. URL_SERVERS.
        server_name: Name of the server.
        nic_name: Name of the NIC.
        name: Name of the firewall rule.
        auth_headers:

    Raises:
        exit(1) if the server does not exist.
    """
    if not server_exists(server_name, api_url, auth_headers):
        exit(1)
    nic_href = name_to_href(server_name, api_url, auth_headers) + "/nics"
    fw_href = name_to_href(nic_name, nic_href, auth_headers) + "/firewallrules"
    fw_rules = json.loads(requests.get(fw_href, headers=auth_headers).text)
    for item in fw_rules["items"]:
        fw_rule = json.loads(requests.get(item["href"], headers=auth_headers).text)
        if fw_rule["properties"]["name"] == name:
            print(
                f"--- Deleting firewallrules.{name} "
                f"from nics.{nic_name} of server.{server_name}."
            )
            requests.delete(item["href"], headers=auth_headers)
    server_href = name_to_href(server_name, api_url, auth_headers)
    all_available([server_href], auth_headers)


def fwrule_delete(api_url, server_name, json_files, auth_headers):
    """Deletes all firewall rules from a NIC of a server.

    Args:
        api_url: URL_SERVERS
        server_name: Name of the server.
        json_files: Contains all server specifications.
        auth_headers:
    """
    nics = (
        json_files[server_name + ".json"]["nics"]
        + json_files[server_name + ".json"]["server"]["entities"]["nics"]["items"]
    )
    for nic in nics:
        for firewallrule in nic["firewallrules"]:
            fwrules_delete_single(
                api_url,
                server_name,
                nic["properties"]["name"],
                firewallrule["properties"]["name"],
                auth_headers,
            )


def math_func(type=None):
    """When deleting anything, this function will ask you a math problem to solve.

    Args:
        type: One of "VOLUME", "NIC", "FIREWALL" or None, i.e., the whole "SERVER".
    """
    input_val = None
    if type:
        if type == "DATACENTER":
            input_val = (
                input(
                    "Are you sure you want to delete the datacenter "
                    f"{os.environ['DATACENTER']}? [y/N]: "
                )
                or "N"
            )
        else:
            input_val = (
                input(
                    f'Are you sure you want to delete {type}="{os.environ[type]}" '
                    f"from server {os.environ['SERVER']}? [y/N]: "
                )
                or "N"
            )
    else:
        input_val = (
            input(
                "Are you sure you want to delete the server "
                f"{os.environ['SERVER']}? [y/N]: "
            )
            or "N"
        )
    if re.match("^(H|h)(elp)?$", input_val):
        print("There is no help.")
        print("Try again.")
        math_func(type)
    elif re.match("^(Y|y)(es)?$", input_val):
        string_val = [
            "zero",
            "one",
            "two",
            "three",
            "four",
            "five",
            "six",
            "seven",
            "eight",
            "nine",
        ]
        left = random.randint(0, 9)
        right = random.randint(0, 9)
        left_str = str(left)
        right_str = str(right)
        if random.choice([True, False]):
            left_str = string_val[left]
        if random.choice([True, False]):
            right_str = string_val[right]
        operator = random.choice(["*", "-", "+"])
        math_problem = f"What is the result of {left_str} {operator} {right_str}? "
        result = None
        if operator == "*":
            result = left * right
        elif operator == "-":
            result = left - right
        elif operator == "+":
            result = left + right
        answer = input(math_problem)
        if not answer or int(answer) != result:
            print("Wrong answer...")
            exit(1)
    elif re.match("^(N|n)(o)?$", input_val):
        print("Ok, see you next time...")
        exit(1)
    else:
        print(f"{input_val} is not a valid answer!")
        math_func(type)


if __name__ == "__main__":
    auth_headers = None
    auth_headers_path = "/datacenters/.auth_headers.json"
    try:
        auth_headers = json_file_open(auth_headers_path)["headers"]
    except Exception as e:
        print(f"Unexpected {e=}, {type(e)=}")
        username = input("Username: ")
        password = getpass.getpass()
        contract_nr = input("Contract Number: ")
        # Function to create the base64 authentication header:
        auth_headers = header_function(username, password, contract_nr)["headers"]

    if "DATACENTER" in os.environ:
        # Check if the requested datacenter is a directory, locally:
        if os.environ.get("DATACENTER") in os.listdir("/datacenters/"):
            path = "/datacenters/" + os.environ["DATACENTER"] + "/*.json"
            json_files = globbing(path)
            server_names = [k.removesuffix(".json") for k in json_files.keys()]
            if "ACTION" not in os.environ:
                print("Environment variable ACTION should be set.")
                exit(1)
        else:
            print(
                "Environment variable DATACENTER should match a local directory name."
            )
            exit(1)
    else:
        print("Environment variable DATACENTER should be set.")
        exit(1)

    URL_SERVERS = (
        name_to_href(os.environ.get("DATACENTER"), URL_DATACENTERS, auth_headers)
        + "/servers"
    )

    if "SERVER" not in os.environ:
        if os.environ["ACTION"] == "create":
            for server_name in server_names:
                create(server_name, json_files, URL_SERVERS, auth_headers)
        elif os.environ["ACTION"] == "delete":
            math_func("DATACENTER")
            for server_name in server_names:
                delete(server_name, json_files, URL_SERVERS, auth_headers)
        else:
            print("Expected values are: ACTION=create or ACTION=delete")
            exit(1)
    else:  # "SERVER" in os.environ
        cnt = 0
        arguments = ["VOLUME", "NIC", "FIREWALLRULE"]
        for argument in arguments:
            if argument in os.environ:
                cnt = cnt + 1
        if cnt > 1:
            print(
                "You cannot create or delete more than one of "
                f"{arguments} at the same time."
            )
            exit(1)
        if os.environ["SERVER"] in server_names and os.environ["ACTION"] == "create":
            if not server_exists(os.environ["SERVER"], URL_SERVERS, auth_headers):
                create(os.environ["SERVER"], json_files, URL_SERVERS, auth_headers)
            else:
                if "VOLUME" in os.environ:
                    attach_single(
                        name_to_href(os.environ["SERVER"], URL_SERVERS, auth_headers),
                        os.environ["SERVER"],
                        os.environ["VOLUME"],
                        "volumes",
                        json_files,
                        auth_headers,
                    )
                elif "NIC" in os.environ:
                    attach_single(
                        name_to_href(os.environ["SERVER"], URL_SERVERS, auth_headers),
                        os.environ["SERVER"],
                        os.environ["NIC"],
                        "nics",
                        json_files,
                        auth_headers,
                    )
                elif "FIREWALLRULE" in os.environ:
                    nics = (
                        json_files[os.environ["SERVER"] + ".json"]["nics"]
                        + json_files[os.environ["SERVER"] + ".json"]["server"][
                            "entities"
                        ]["nics"]["items"]
                    )
                    for nic in nics:
                        if "firewallrules" in nic:
                            rules = [
                                rule["properties"]["name"]
                                for rule in nic["firewallrules"]
                            ]
                            for rule in rules:
                                matched = re.match(
                                    os.environ["FIREWALLRULE"],
                                    rule,
                                )
                                if matched:
                                    fwrules_create_single(
                                        rule,
                                        os.environ["SERVER"],
                                        nic["properties"]["name"],
                                        json_files,
                                        auth_headers,
                                    )
                else:
                    exit(1)
        elif os.environ["SERVER"] in server_names and os.environ["ACTION"] == "delete":
            if server_exists(os.environ["SERVER"], URL_SERVERS, auth_headers):
                if "VOLUME" in os.environ:
                    math_func("VOLUME")
                    server_href = name_to_href(
                        os.environ["SERVER"], URL_SERVERS, auth_headers
                    )
                    item_href = name_to_href(
                        os.environ["VOLUME"],
                        server_href + "/" + "volumes",
                        auth_headers,
                    )
                    detach_single(
                        item_href,
                        os.environ["SERVER"],
                        os.environ["VOLUME"],
                        "volumes",
                        json_files,
                        auth_headers,
                    )
                elif "NIC" in os.environ:
                    math_func("NIC")
                    server_href = name_to_href(
                        os.environ["SERVER"], URL_SERVERS, auth_headers
                    )
                    item_href = name_to_href(
                        os.environ["NIC"], server_href + "/" + "nics", auth_headers
                    )
                    detach_single(
                        item_href,
                        os.environ["SERVER"],
                        os.environ["NIC"],
                        "nics",
                        json_files,
                        auth_headers,
                    )
                elif "FIREWALLRULE" in os.environ:
                    math_func("FIREWALLRULE")
                    nics = (
                        json_files[os.environ["SERVER"] + ".json"]["nics"]
                        + json_files[os.environ["SERVER"] + ".json"]["server"][
                            "entities"
                        ]["nics"]["items"]
                    )
                    for nic in nics:
                        if "firewallrules" in nic:
                            rules = [
                                firewallrule["properties"]["name"]
                                for firewallrule in nic["firewallrules"]
                            ]
                            for rule in rules:
                                matched = re.match(
                                    os.environ["FIREWALLRULE"],
                                    rule,
                                )
                                if matched:
                                    fwrules_delete_single(
                                        URL_SERVERS,
                                        os.environ["SERVER"],
                                        nic["properties"]["name"],
                                        rule,
                                        auth_headers,
                                    )
                else:
                    math_func()
                    delete(os.environ["SERVER"], json_files, URL_SERVERS, auth_headers)
            else:
                exit(1)
        else:
            print("Excpected ACTION to be one of: create, delete")
            print(f"Expected server name to be one of: {server_names}")
            exit(1)

    print("=== Done!")
    print("=== Total time: ", (time.time() - start))
    sys.stdout.flush()
    exit(0)
