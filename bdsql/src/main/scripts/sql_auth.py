#
# Copyright 2018 herd-mdl contributors
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
import re, sys
import boto3
import ldap
import pyhs2
import MySQLdb
import MySQLdb.cursors
import datetime
import requests
from xml.dom import minidom

try:
    action = sys.argv[1]
except Exception as e:
    action = None


def log(msg, log_level='info'):
    now = datetime.datetime.now()
    print "{} - {} - {}".format(now, log_level, msg)


def get_boto_session():
    return boto3.Session()

def get_s3_home_bucket():

    session = get_boto_session()
    client = session.client('ec2')

    account_id = session.client('sts').get_caller_identity()['Account']
    instance_id = requests.get('http://169.254.169.254/latest/meta-data/instance-id').text
    tag_response = client.describe_tags(
        Filters=[{'Name':'resource-id', 'Values' : [instance_id]}])
    tags = {}
    for tag in tag_response['Tags']:
        tags[tag['Key']] = tag['Value']

    if 'Purpose' in tags and 'Environment' in tags:
        s3_home_bucket = '{}-{}-mdl-{}'.format(
            account_id, tags['Purpose'], tags['Environment']).lower()
    else:
        s3_home_bucket = '{}-staging'.format(account_id)

    return s3_home_bucket

def get_ldap_info():

    ldap_info = {
        'base_dn' : None,
        'hostname' : None,
    }

    session = get_boto_session()
    ssm_client = session.client('ssm')

    for k in ldap_info.keys():

        ldap_info_response = ssm_client.get_parameter(Name='/mdl/ldap/{}'.format(k))
        ldap_info[k] = ldap_info_response['Parameter']['Value']

    return ldap_info


def get_metastor_info():

    info = {
        'hostname' : None,
        'user' : None,
        'pass' : None
    }

    doc = minidom.parse('/etc/hive/conf/hive-site.xml')
    items = doc.getElementsByTagName('property')
    for elem in items:
        name = elem.getElementsByTagName('name')[0].childNodes[0].nodeValue
        if len(elem.getElementsByTagName('value')[0].childNodes) > 0:
            value = elem.getElementsByTagName('value')[0].childNodes[0].nodeValue
        else:
            value = None
        if name == 'javax.jdo.option.ConnectionURL':
            info['hostname'] = value.split('/')[2].split(':')[0]
        elif name == 'javax.jdo.option.ConnectionUserName':
            info['user'] = value
        elif name == 'javax.jdo.option.ConnectionPassword':
            info['pass'] = value

    return info


def get_creds(purpose):


    creds = {
        'user' : None,
        'pass' : None,
    }

    session = get_boto_session()
    ssm_client = session.client('ssm')

    for k in creds.keys():

        cred_response = ssm_client.get_parameter(Name='/mdl/{}/app_{}'.format(purpose,k))
        creds[k] = cred_response['Parameter']['Value']

    return creds


def get_hive_conn(database='default'):

    creds = get_creds('ldap')

    hive_conn = pyhs2.connect(
        host='localhost',
        port=10000,
        authMechanism="PLAIN",
        user=creds['user'],
        password=creds['pass'],
        database=database)

    return hive_conn


def execute_hive_query(database,q):

    # initialize Hive connection and cursor
    hive_conn = get_hive_conn(database)
    cur = hive_conn.cursor()

    # execute multiple queries if q is a list
    if isinstance(q,list):
        for i in q:
            cur.execute(i)
    else:
        cur.execute(q)

    # if result set, create list of dictionaries
    data = []
    has_result_set = bool(vars(cur.operationHandle)['hasResultSet'])
    if has_result_set:
        schema = cur.getSchema()
        if schema:
            columns = [i['columnName'].split('.')[-1] for i in schema]
        else:
            columns = []
        rs = cur.fetchall()
        for row in rs:
            data.append(dict(zip(columns,row)))

    return data


def get_metastor_conn():

    metastor_info = get_metastor_info()

    metastor_conn = MySQLdb.connect(
        host=metastor_info['hostname'],
        user=metastor_info['user'],
        passwd=metastor_info['pass'],
        db="metastor",
        cursorclass=MySQLdb.cursors.DictCursor,
        ssl={'ca' : '/etc/hive/conf/rds-combined-ca-bundle.pem'})

    return metastor_conn


def execute_metastor_query(q):

    data = []

    metastor_conn = get_metastor_conn()

    cur = metastor_conn.cursor()
    cur.execute(q)

    for row in cur.fetchall():
        data.append(row)

    if q.split(' ')[0].lower() in ['update','insert','delete']:
        metastor_conn.commit()

    metastor_conn.close()

    return data


def get_metastor_objects():

    q = """
    SELECT
        d.DB_ID,
        d.NAME,
        d.OWNER_NAME,
        d.OWNER_TYPE,
        d.DB_LOCATION_URI,
        t.TBL_ID,
        t.TBL_NAME,
        t.CREATE_TIME,
        t.OWNER,
        t.TBL_TYPE
    FROM
        DBS d
    LEFT JOIN
        TBLS t ON(d.DB_ID=t.DB_ID)
    WHERE
        NOT d.NAME='default' AND
        DB_LOCATION_URI RLIKE 's3://.*/METASTOR/.*\.db$'"""

    data = execute_metastor_query(q)

    return data


def get_user_objects():

    #hdfs://ip-10-10-146-14.ec2.internal:8020/user/hive/warehouse/user_test_1.db
    #s3://328703175456-us-east-1-staging/MDL/BDSQL/home/user_mdl_app.db
    #s3://328703175456-us-east-1-appdata/MDL/BDSQL/home/user_mdl_app.db
    #s3://328703175456-us-east-1-bdsql/home/user_mdl_app.db

    q = """
    SELECT
        d.DB_ID,
        d.NAME,
        d.OWNER_NAME,
        d.OWNER_TYPE,
        d.DB_LOCATION_URI,
        t.TBL_ID,
        t.TBL_NAME,
        t.CREATE_TIME,
        t.OWNER,
        t.TBL_TYPE
    FROM
        DBS d
    LEFT JOIN
        TBLS t ON(d.DB_ID=t.DB_ID)
    WHERE
        NOT d.NAME='default' AND
        DB_LOCATION_URI RLIKE 'user_.*\.db$'"""

    data = execute_metastor_query(q)

    return data


def get_roles_users():

    q = """
    SELECT
        r.ROLE_NAME role_name,
        rm.PRINCIPAL_NAME user
    FROM
        ROLES r
    JOIN
        ROLE_MAP rm ON(rm.ROLE_ID=r.ROLE_ID)
    WHERE
        PRINCIPAL_TYPE='USER'
    ORDER BY
        role_name"""

    data = execute_metastor_query(q)

    return data


def get_ldap_conn():

    creds = get_creds('ldap')
    ldap_info = get_ldap_info()

    full_bind_user='uid=%s,ou=People,%s' % (creds['user'],ldap_info['base_dn'])

    ldap.set_option(ldap.OPT_X_TLS_REQUIRE_CERT, ldap.OPT_X_TLS_NEVER)
    ldap.set_option(ldap.OPT_REFERRALS,0)

    ldap_conn = ldap.initialize('ldaps://{}:636'.format(ldap_info['hostname']))
    ldap_conn.protocol_version = ldap.VERSION3
    ldap_conn.simple_bind_s(full_bind_user, creds['pass'])

    return ldap_conn


def execute_ldap_query(search_filter):

    data = []

    ldap_conn = get_ldap_conn()
    ldap_info = get_ldap_info()

    search_scope = ldap.SCOPE_SUBTREE
    result_attributes = None
    ldap_result = ldap_conn.search_s(
        ldap_info['base_dn'], search_scope,
        search_filter, result_attributes)

    data = []
    for i in ldap_result:
        data.append(i)
    return data


def get_ldap_objects():

    search_filter = '(&(cn=APP_MDL_*))'
    data = execute_ldap_query(search_filter)
    return data


def convert_database_name_to_role(database, ldap_objects):

    acl_match = None

    for ldap_object in ldap_objects:
        cn_name = ldap_object[1]['cn'][0]

        regex = re.compile(r'^APP_MDL_ACL_(RO|RW|W)_{}'.format(database), re.I)
        matches = regex.search(cn_name)
        if matches:
            acl_match = cn_name

    if acl_match is None:
        acl_match = 'APP_MDL_ACL_RO_public'

    return acl_match.lower()


def create_hive_role(database, role_name):

    q = """
        SELECT
            IFNULL(COUNT(*),0) AS CNT
        FROM
            ROLES r
        WHERE
            ROLE_NAME='{}' LIMIT 1""".format(role_name)
    role_exists = execute_metastor_query(q)[0]['CNT']

    if role_exists == 0:
        log("creating role.")
        create_query = "CREATE ROLE {}".format(role_name)
        qs = ['set role admin',create_query]
        execute_hive_query(database,qs)

    # todo: with grant option
    if re.search('_ACL_(RO|RW|R|W)_public$', role_name):
        with_grant=""
    else:
        with_grant=" WITH GRANT OPTION"

    if re.search('_ACL_RO_',role_name, re.I):
        privs = ['SELECT']

    tables = execute_hive_query(database,"show tables")
    for table in tables:
        for priv in privs:

            e = """
            SELECT
                tp.GRANTOR,
                d.NAME,
                t.TBL_NAME,
                tp.TBL_PRIV,
                tp.GRANT_OPTION,
                tp.PRINCIPAL_TYPE,
                tp.PRINCIPAL_NAME
            FROM
                TBLS t
            JOIN
                DBS d ON(d.DB_ID=t.DB_ID)
            LEFT JOIN
                TBL_PRIVS tp ON(tp.TBL_ID=t.TBL_ID)
            WHERE
                d.NAME='{}' AND
                t.TBL_NAME='{}' AND
                tp.TBL_PRIV='{}' AND
                tp.PRINCIPAL_NAME='{}'""".format(
                database, table['tab_name'], priv, role_name.lower())
            exists_data = execute_metastor_query(q)

            if len(exists_data) == 0:
                log("#1.a creating new privilege on table: table={} priv={}".format(
                    table['tab_name'], priv))
                grant_query="GRANT {} ON TABLE {}.{} TO ROLE {}".format(
                    priv, database, table['tab_name'], role_name)
                execute_hive_query(database,['set role admin',grant_query])
            else:
                log("#1.b privilege already exists on table: table={} priv={}".format(
                    table['tab_name'], priv))


def add_users_to_role(database, role_name, ldap_objects):

    for ldap_object in ldap_objects:
        if (ldap_object[1]['cn'][0].lower() == 'app_mdl_users' and role_name == 'app_mdl_acl_ro_public') or \
                        ldap_object[1]['cn'][0].lower() == role_name:
            users = [i.split(',')[0].replace('uid=','').strip() for i in ldap_object[1]['member']]
            for user in users:
                q = """
                SELECT
                    *
                FROM
                    ROLE_MAP rm
                JOIN
                    ROLES r ON (r.ROLE_ID=rm.ROLE_ID)
                WHERE
                    rm.PRINCIPAL_NAME='{}' AND
                    r.ROLE_NAME='{}' LIMIT 1""".format(user, role_name)
                exists_data = execute_metastor_query(q)
                log("started on user. user={}".format(user))
                if len(exists_data) == 0:
                    log("adding user.")
                    grant_query="GRANT ROLE {} TO USER {}".format(
                        role_name, user)
                    execute_hive_query(database,['set role admin',grant_query])
                else:
                    log("user is already associated with role.")


def grant_privs_to_tables(database, role_name):

    tables = execute_hive_query(database,"show tables")

    if re.search('_ACL_RO_',role_name, re.I):
        privs = ['SELECT']

    for table in tables:
        for priv in privs:
            exists_q = 'show grant role {} on table {}'.format(role_name, table['tab_name'])
            exists_data = execute_hive_query(database,['set role admin', exists_q])
            priv_exists = [i for i in exists_data if i['privilege'] == priv]
            if len(priv_exists) == 0:
                log("#2.a creating new privilege on table. table={} priv={}".format(
                    table['tab_name'], priv))
                q = "GRANT {} ON TABLE {}.{} TO ROLE {}".format(priv, database, table['tab_name'], role_name)
                execute_hive_query(database,['set role admin',q])
            else:
                log("#2.b privilege already exists on table. table={} priv={}".format(
                    table['tab_name'], priv))


def sync_app_objects():

    log("started syncing app objects")

    metastor_objects = get_metastor_objects()
    ldap_objects = get_ldap_objects()

    databases = set(sorted([i['NAME'] for i in metastor_objects],reverse=True))
    log("total_dbs={}".format(len(databases)))
    for database in databases:
        role_name = convert_database_name_to_role(database,ldap_objects)
        log("started on database/role: database={} role_name={}".format(database, role_name))
        create_hive_role(database, role_name)
        add_users_to_role(database, role_name, ldap_objects)
        grant_privs_to_tables(database, role_name)


def sync_user_objects():

    log("started syncing user objects")

    ldap_objects = get_ldap_objects()
    roles_users = get_roles_users()

    privs = ['SELECT','INSERT','UPDATE','DELETE']

    s3_home_bucket = get_s3_home_bucket()

    users = []
    for ldap_object in ldap_objects:
        cn = ldap_object[1]['cn'][0]
        if cn == 'APP_MDL_Users':
            users = [i.split(',')[0].replace('uid=','').strip() for i in ldap_object[1]['member']]

    log("total_users={}".format(len(users)))

    for user in users:
        user_schema_name = 'user_{}'.format(user)
        user_schema_location = 's3://{}/BDSQL/home/{}.db'.format(s3_home_bucket,user_schema_name)
        user_schemas = [i['database_name'] for i in execute_hive_query('default','show schemas')]

        log("started on user: user={} user_schema={} s3_location={}".format(
            user, user_schema_name, user_schema_location))

        if user_schema_name not in user_schemas:
            log("creating new schema.")
            cq = "CREATE SCHEMA IF NOT EXISTS {} LOCATION '{}'".format(
                user_schema_name, user_schema_location)
            aq = "ALTER SCHEMA {} SET OWNER USER {}".format(
                user_schema_name, user)
            queries = ['set role admin', cq, aq]
            execute_hive_query('default',queries)
        else:
            log("schema already exists.")

        db_schema_location = execute_metastor_query(
            "SELECT db_location_uri FROM DBS WHERE NAME='{}' LIMIT 1".format(
                user_schema_name))[0]['db_location_uri']

        if user_schema_location != db_schema_location:
            log("sanity check failure, schema location. db_schema_location={}".format(
                db_schema_location))
            q = "update DBS set DB_LOCATION_URI = '{}' WHERE name='{}' LIMIT 1".format(
                user_schema_location, user_schema_name)
            execute_metastor_query(q)
        else:
            log("sanity check passed, schema location. db_schema_location={}".format(
                db_schema_location))

        sds_entries = execute_metastor_query(
            "SELECT * from SDS where location rlike '{}' LIMIT 1".format(user_schema_name))
        if len(sds_entries) > 0:
            log("found {} SDS entries".format(len(sds_entries)))
        else:
            log("no SDS entries found")

        tables = execute_hive_query(user_schema_name,'show tables')
        if len(tables) > 0:
            log("found table entries")
            for table in tables:
                for priv in privs:
                    log("started working on user table: table={} priv={}".format(
                        table['tab_name'], priv))
                    priv_exists_query = """
                    SELECT
                        d.name,
                        t.tbl_name,
                        tp.tbl_priv,
                        tp.GRANT_OPTION,
                        tp.PRINCIPAL_NAME,
                        tp.TBL_GRANT_ID
                    FROM
                        DBS d
                    LEFT JOIN
                        TBLS t ON(d.DB_ID=t.DB_ID)
                    LEFT JOIN
                        TBL_PRIVS tp ON(tp.TBL_ID=t.TBL_ID)
                    WHERE
                        d.NAME='{}' AND
                        t.tbl_name='{}' AND
                        tp.tbl_priv='{}' LIMIT 1""".format(
                        user_schema_name, table['tab_name'], priv)
                    priv_exists_data = execute_metastor_query(priv_exists_query)

                    if len(priv_exists_data) == 0:
                        log("#3.a creating new privilege on table: table={} priv={}".format(
                            table['tab_name'], priv))
                        q = "GRANT {} ON TABLE {}.{} TO USER {} WITH GRANT OPTION".format(
                            priv, user_schema_name, table['tab_name'], user)
                        execute_hive_query(user_schema_name,['set role admin',q])
                    else:
                        log("#3.a privilege already exists on table: table={} priv={}".format(
                            table['tab_name'], priv))
        else:
            log("no table entries found")

    # ensure users are removed from Hive SQL authorization role if user is remove from LDAP group.
    for ldap_object in ldap_objects:

        ldap_group_cn = ldap_object[1]['cn'][0].lower()

        if ldap_group_cn == 'app_mdl_users':
            ldap_group_role_match = 'app_mdl_acl_ro_public'
        else:
            ldap_group_role_match = ldap_group_cn

        ldap_users = [user.split(',')[0].replace('uid=','') for user in ldap_object[1]['member']]
        role_users = [role_user['user'] for role_user in roles_users
                      if role_user['role_name'] == ldap_group_role_match]

        for role_user in role_users:
            if role_user not in ldap_users:
                log("{} user does not belong to the {} LDAP group. removing user from role {}.".format(
                    role_user, ldap_group_cn, ldap_group_role_match), 'warn')
                q = "REVOKE ROLE {} FROM USER {}".format(ldap_group_role_match, role_user)
                execute_hive_query('default',['set role admin',q])

def remove_roles():

    roles = execute_hive_query('default',['set role admin','show roles'])
    acl_roles = [i['role'] for i in roles if '_acl_' in i['role'].lower()]

    for acl_role in acl_roles:

        log("started removing role. role={}".format(acl_role))
        principal_data = execute_hive_query('default',['set role admin','SHOW PRINCIPALS {}'.format(acl_role)])

        for principal in principal_data:

            user = principal['principal_name']
            log("started removing user from role. role={} user={}".format(acl_role, user))

            q = "REVOKE ROLE {} FROM USER {}".format(acl_role,user)
            execute_hive_query('default',['set role admin',q])

        q = "DROP ROLE {}".format(acl_role)
        execute_hive_query('default',['set role admin',q])

        log("finished with role. role={}".format(acl_role))


def remove_privs():

    revokes = []

    databases = execute_metastor_query("select * from DBS")
    for database in databases:
        tables = execute_hive_query(database['NAME'],"SHOW TABLES")
        for table in tables:
            log("started on object. database={} table={}".format(database['NAME'], table['tab_name']))
            q = "show grant on table {}".format(table['tab_name'])
            priv_data = execute_hive_query(database['NAME'], ['set role admin',q])
            for i in priv_data:
                log("revoking priv. priv={} principal={} principal_name={} grant_option={}  database={} table={}".format(
                    i['privilege'], i['principal_type'], i['principal_name'], i['grant_option'], database['NAME'], i['table']))
                revoke_query = "REVOKE {} ON {} FROM {} {}".format(
                    i['privilege'], i['table'], i['principal_type'], i['principal_name'])
                execute_hive_query(database['NAME'],['set role admin',revoke_query])
                revokes.append(i)
            log("finished with object")

    log("revoked {} privs".format(len(revokes)))


def remove_user_objects():

    log("started removing user objects")
    ldap_objects = get_ldap_objects()

    s3_home_bucket = get_s3_home_bucket()

    users = []
    for ldap_object in ldap_objects:
        cn = ldap_object[1]['cn'][0]
        if cn == 'APP_MDL_Users':
            users = [i.split(',')[0].replace('uid=','').strip() for i in ldap_object[1]['member']]

    log("total_users={}".format(len(users)))

    for user in users:
        user_schema_name = 'user_{}'.format(user)
        user_schema_location = 's3://{}/BDSQL/home/{}.db'.format(s3_home_bucket,user_schema_name)
        user_schemas = [i['database_name'] for i in execute_hive_query('default','show schemas')]

        log("started on user: user={} user_schema={} s3_location={}".format(
            user, user_schema_name, user_schema_location))

        if user_schema_name in user_schemas:
            log("removing schema.")
            q = "DROP SCHEMA IF EXISTS {} CASCADE".format(user_schema_name)
            execute_hive_query('default',['set role admin',q])
        else:
            log("schema doesn't exist.")


def show_acls():

    log("show access control list")

    print "### LDAP ###"
    ldap_objects = get_ldap_objects()
    for ldap_object in ldap_objects:
        group_cn = str(ldap_object[0]) #.split(',')[0].replace('cn=','')
        print "group:\n\t{}".format(group_cn)
        print 'members:\n\t' + '\n\t'.join(ldap_object[1]['member'])
        print '\n'.strip()

    print "### Hive ###"
    roles = execute_hive_query('default',['set role admin','show roles'])
    acl_roles = [i['role'] for i in roles if '_acl_' in i['role'].lower()]
    for acl_role in acl_roles:
        print "role:\n\t{}".format(acl_role)
        data = execute_hive_query('default',['set role admin','SHOW PRINCIPALS {}'.format(acl_role)])
        headers = data[0].keys()
        print 'members:\n\t' + ','.join(headers)
        for d in data:
            print '\t' + ','.join([str(i) for i in d.values()])

    databases = execute_metastor_query("SELECT * FROM DBS")
    for database in databases:
        print "working on db: {}".format(database['NAME'])
        tables = execute_hive_query(database['NAME'],"SHOW TABLES")
        q = "select tp.GRANTOR, tp.PRINCIPAL_TYPE, tp.PRINCIPAL_NAME, d.NAME, t.TBL_NAME, t.TBL_TYPE, t.CREATE_TIME, t.TBL_ID from DBS d JOIN TBLS t on(t.DB_ID=d.DB_ID) LEFT JOIN TBL_PRIVS tp ON(tp.TBL_ID=t.TBL_ID) WHERE d.NAME='{}'".format(database['NAME'])
        data = execute_metastor_query(q)
        if len(data) > 0:
            headers = data[0].keys()
            print '\t' + ','.join(headers)
            for d in data:
                print '\t' + ','.join([str(i) for i in d.values()])
            print '\n'.strip()


if action == 'sync_app_objects':
    sync_app_objects()

elif action == 'sync_user_objects':
    sync_user_objects()

elif action == 'remove_privs':
    remove_privs()

elif action == 'remove_roles':
    remove_roles()

elif action == 'remove_user_objects':
    remove_user_objects()

elif action == 'show_acls':
    show_acls()

else:
    log("invalid action: action={}".format(action), "fatal")
