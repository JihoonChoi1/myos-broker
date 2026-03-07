# Django's MySQL backend imports MySQLdb (mysqlclient), which needs native
# MySQL client libs to build on macOS. PyMySQL is pure Python and registers
# itself under that module name.
import pymysql

pymysql.install_as_MySQLdb()
