"""Unit tests for Azure SQL hostname detection (CodeQL-safe normalization)."""

from services.sql_server_host import normalized_sql_server_hostname, sql_server_host_is_azure_sql


def test_normalized_tcp_comma_port():
    assert normalized_sql_server_hostname("tcp:myserver.database.windows.net,1433") == "myserver.database.windows.net"


def test_normalized_plain_azure_host():
    assert normalized_sql_server_hostname("myserver.database.windows.net") == "myserver.database.windows.net"


def test_normalized_host_colon_port():
    assert normalized_sql_server_hostname("myserver.database.windows.net:1433") == "myserver.database.windows.net"


def test_normalized_https_url_uses_hostname_only():
    assert (
        normalized_sql_server_hostname("https://myserver.database.windows.net/")
        == "myserver.database.windows.net"
    )


def test_path_like_host_rejected():
    assert normalized_sql_server_hostname("evil.com/database.windows.net") == ""


def test_azure_sql_true_for_logical_server():
    assert sql_server_host_is_azure_sql("myserver.database.windows.net") is True
    assert sql_server_host_is_azure_sql("tcp:myserver.database.windows.net,1433") is True


def test_azure_sql_false_for_on_prem_style():
    assert sql_server_host_is_azure_sql("sql.contoso.local") is False
    assert sql_server_host_is_azure_sql("localhost") is False
