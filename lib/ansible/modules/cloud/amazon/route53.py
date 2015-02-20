#!/usr/bin/python
# This file is part of Ansible
#
# Ansible is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# Ansible is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with Ansible.  If not, see <http://www.gnu.org/licenses/>.

DOCUMENTATION = '''
---
module: route53
version_added: "1.3"
short_description: add or delete entries in Amazons Route53 DNS service
description:
     - Creates and deletes DNS records in Amazons Route53 service
options:
  command:
    description:
      - Specifies the action to take.  
    required: true
    default: null
    aliases: []
    choices: [ 'get', 'create', 'delete' ]
  zone:
    description:
      - The DNS zone to modify
    required: true
    default: null
    aliases: []
  record:
    description:
      - The full DNS record to create or delete
    required: true
    default: null
    aliases: []
  ttl:
    description:
      - The TTL to give the new record
    required: false
    default: 3600 (one hour)
    aliases: []
  type:
    description:
      - The type of DNS record to create
    required: true
    default: null
    aliases: []
    choices: [ 'A', 'CNAME', 'MX', 'AAAA', 'TXT', 'PTR', 'SRV', 'SPF', 'NS' ]
  alias:
    description:
      - Indicates if this is an alias record.
    required: false
    version_added: 1.9
    default: False
    aliases: []
    choices: [ 'True', 'False' ]
  alias_hosted_zone_id:
    description:
      - The hosted zone identifier.
    required: false
    version_added: 1.9
    default: null
    aliases: []
  value:
    description:
      - The new value when creating a DNS record.  Multiple comma-spaced values are allowed for non-alias records.  When deleting a record all values for the record must be specified or Route53 will not delete it.
    required: false
    default: null
    aliases: []
  overwrite:
    description:
      - Whether an existing record should be overwritten on create if values do not match
    required: false
    default: null
    aliases: []
  retry_interval:
    description:
      - In the case that route53 is still servicing a prior request, this module will wait and try again after this many seconds. If you have many domain names, the default of 500 seconds may be too long.
    required: false
    default: 500
    aliases: []
  private_zone:
    description:
      - If set to true, the private zone matching the requested name within the domain will be used if there are both public and private zones. The default is to use the public zone.
    required: false
    default: false
    version_added: "1.9"
  identifier:
    description:
      - Weighted and latency-based resource record sets only. An identifier
        that differentiates among multiple resource record sets that have the
        same combination of DNS name and type.
    required: false
    default: null
    version_added: "2.0"
  weight:
    description:
      - Weighted resource record sets only. Among resource record sets that
        have the same combination of DNS name and type, a value that
        determines what portion of traffic for the current resource record set
        is routed to the associated location.
    required: false
    default: null
    version_added: "2.0"
  region:
    description:
      - Latency-based resource record sets only Among resource record sets
        that have the same combination of DNS name and type, a value that
        determines which region this should be associated with for the
        latency-based routing
    required: false
    default: null
    version_added: "2.0"
  health_check:
    description:
      - Health check to associate with this record
    required: false
    default: null
    version_added: "2.0"
  failover:
    description:
      - Failover resource record sets only. Whether this is the primary or
        secondary resource record set.
    required: false
    default: null
    version_added: "2.0"
author: "Bruce Pennypacker (@bpennypacker)"
extends_documentation_fragment: aws
'''

# FIXME: the command stuff should have a more state like configuration alias -- MPD

EXAMPLES = '''
# Add new.foo.com as an A record with 3 IPs
- route53:
      command: create
      zone: foo.com
      record: new.foo.com
      type: A
      ttl: 7200
      value: 1.1.1.1,2.2.2.2,3.3.3.3

# Retrieve the details for new.foo.com
- route53:
      command: get
      zone: foo.com
      record: new.foo.com
      type: A
  register: rec

# Delete new.foo.com A record using the results from the get command
- route53:
      command: delete
      zone: foo.com
      record: "{{ rec.set.record }}"
      ttl: "{{ rec.set.ttl }}"
      type: "{{ rec.set.type }}"
      value: "{{ rec.set.value }}"

# Add an AAAA record.  Note that because there are colons in the value
# that the entire parameter list must be quoted:
- route53:
      command: "create"
      zone: "foo.com"
      record: "localhost.foo.com"
      type: "AAAA"
      ttl: "7200"
      value: "::1"

# Add a TXT record. Note that TXT and SPF records must be surrounded
# by quotes when sent to Route 53:
- route53:
      command: "create"
      zone: "foo.com"
      record: "localhost.foo.com"
      type: "TXT"
      ttl: "7200"
      value: '"bar"'

# Add an alias record that points to an Amazon ELB:
- route53:
      command=create
      zone=foo.com
      record=elb.foo.com
      type=A
      value="{{ elb_dns_name }}"
      alias=True
      alias_hosted_zone_id="{{ elb_zone_id }}"

# Use a routing policy to distribute traffic:
- route53:
      command: "create"
      zone: "foo.com"
      record: "www.foo.com"
      type: "CNAME"
      value: "host1.foo.com"
      ttl: 30
      # Routing policy
      identifier: "host1@www"
      weight: 100
      health_check: "d994b780-3150-49fd-9205-356abdd42e75"

'''

import time

try:
    import boto
    from boto import route53
    from boto.route53 import Route53Connection
    from boto.route53.record import Record, ResourceRecordSets
    HAS_BOTO = True
except ImportError:
    HAS_BOTO = False

def get_zone_by_name(conn, module, zone_name, want_private):
    """Finds a zone by name"""
    for zone in conn.get_zones():
        # only save this zone id if the private status of the zone matches
        # the private_zone_in boolean specified in the params
        private_zone = module.boolean(zone.config.get('PrivateZone', False))
        if private_zone == want_private and zone.name == zone_name:
            return zone
    return None


def commit(changes, retry_interval):
    """Commit changes, but retry PriorRequestNotComplete errors."""
    retry = 10
    while True:
        try:
            retry -= 1
            return changes.commit()
        except boto.route53.exception.DNSServerError, e:
            code = e.body.split("<Code>")[1]
            code = code.split("</Code>")[0]
            if code != 'PriorRequestNotComplete' or retry < 0:
                raise e
            time.sleep(float(retry_interval))

def main():
    argument_spec = ec2_argument_spec()
    argument_spec.update(dict(
            command              = dict(choices=['get', 'create', 'delete'], required=True),
            zone                 = dict(required=True),
            record               = dict(required=True),
            ttl                  = dict(required=False, default=3600),
            type                 = dict(choices=['A', 'CNAME', 'MX', 'AAAA', 'TXT', 'PTR', 'SRV', 'SPF', 'NS'], required=True),
            alias                = dict(required=False, type='bool'),
            alias_hosted_zone_id = dict(required=False),
            value                = dict(required=False),
            overwrite            = dict(required=False, type='bool'),
            retry_interval       = dict(required=False, default=500)
            private_zone         = dict(required=False, type='bool', default=False),
            identifier           = dict(required=False),
            weight               = dict(required=False, type='int'),
            region               = dict(required=False),
            health_check         = dict(required=False),
            failover             = dict(required=False),
        )
    )
    module = AnsibleModule(argument_spec=argument_spec)

    if not HAS_BOTO:
        module.fail_json(msg='boto required for this module')

    command_in              = module.params.get('command')
    zone_in                 = module.params.get('zone').lower()
    ttl_in                  = int(module.params.get('ttl'))
    record_in               = module.params.get('record').lower()
    type_in                 = module.params.get('type')
    value_in                = module.params.get('value')
    alias_hosted_zone_id_in = module.params.get('alias_hosted_zone_id')
    retry_interval_in       = module.params.get('retry_interval')
    private_zone_in         = module.params.get('private_zone')
    identifier_in           = module.params.get('identifier')
    weight_in               = module.params.get('weight')
    region_in               = module.params.get('region')
    health_check_in         = module.params.get('health_check')
    failover_in             = module.params.get('failover')

    region, ec2_url, aws_connect_kwargs = get_aws_connection_info(module)

    value_list = ()

    if type(value_in) is str:
        if value_in:
            value_list = sorted(value_in.split(','))
    elif type(value_in)  is list:
        value_list = sorted(value_in)

    if zone_in[-1:] != '.':
        zone_in += "."

    if record_in[-1:] != '.':
        record_in += "."

    if command_in == 'create' or command_in == 'delete':
        if not value_in:
            module.fail_json(msg = "parameter 'value' required for create/delete")
        elif module.params['alias']:
          if len(value_list) != 1:
              module.fail_json(msg = "parameter 'value' must contain a single dns name for alias create/delete")
          elif not alias_hosted_zone_id_in:
              module.fail_json(msg = "parameter 'alias_hosted_zone_id' required for alias create/delete")

    # connect to the route53 endpoint 
    try:
        conn = Route53Connection(**aws_connect_kwargs)
    except boto.exception.BotoServerError, e:
        module.fail_json(msg = e.error_message)

    # Find the named zone ID
    zone = get_zone_by_name(conn, module, zone_in, private_zone_in)

    # Verify that the requested zone is already defined in Route53
    if zone is None:
        errmsg = "Zone %s does not exist in Route53" % zone_in
        module.fail_json(msg = errmsg)

    record = {}
    
    found_record = False
    wanted_rset = Record(name=record_in, type=type_in, ttl=ttl_in,
        identifier=identifier_in, weight=weight_in, region=region_in,
        health_check=health_check_in, failover=failover_in)
    for v in value_list:
        if alias_in:
            wanted_rset.set_alias(alias_hosted_zone_id_in, v)
        else:
            wanted_rset.add_value(v)

    sets = conn.get_all_rrsets(zone.id, name=record_in, type=type_in, identifier=identifier_in)
    for rset in sets:
        # Due to a bug in either AWS or Boto, "special" characters are returned as octals, preventing round
        # tripping of things like * and @.
        decoded_name = rset.name.replace(r'\052', '*')
        decoded_name = decoded_name.replace(r'\100', '@')

        if rset.type == type_in and decoded_name.lower() == record_in.lower() and rset.identifier == identifier_in:
            found_record = True
            record['zone'] = zone_in
            record['type'] = rset.type
            record['record'] = decoded_name
            record['ttl'] = rset.ttl
            record['value'] = ','.join(sorted(rset.resource_records))
            record['values'] = sorted(rset.resource_records)
            record['identifier'] = rset.identifier
            record['weight'] = rset.weight
            record['region'] = rset.region
            record['failover'] = rset.failover
            record['health_check'] = rset.health_check
            if rset.alias_dns_name:
              record['alias'] = True
              record['value'] = rset.alias_dns_name
              record['values'] = [rset.alias_dns_name]
              record['alias_hosted_zone_id'] = rset.alias_hosted_zone_id
            else:
              record['alias'] = False
              record['value'] = ','.join(sorted(rset.resource_records))
              record['values'] = sorted(rset.resource_records)
            if command_in == 'create' and rset.to_xml() == wanted_rset.to_xml():
                module.exit_json(changed=False)
            break

    if command_in == 'get':
        if type_in == 'NS':
            ns = record['values']
        else:
            # Retrieve name servers associated to the zone.
            ns = conn.get_zone(zone_in).get_nameservers()

        module.exit_json(changed=False, set=record, nameservers=ns)

    if command_in == 'delete' and not found_record:
        module.exit_json(changed=False)

    changes = ResourceRecordSets(conn, zone.id)

    if command_in == 'create' or command_in == 'delete':
        if command_in == 'create' and found_record:
            if not module.params['overwrite']:
                module.fail_json(msg = "Record already exists with different value. Set 'overwrite' to replace it")
            command = 'UPSERT'
        else:
            command = command_in.upper()
        changes.add_change_record(command, wanted_rset)

    try:
        result = commit(changes, retry_interval_in)
    except boto.route53.exception.DNSServerError, e:
        txt = e.body.split("<Message>")[1]
        txt = txt.split("</Message>")[0]
        module.fail_json(msg = txt)

    module.exit_json(changed=True)

# import module snippets
from ansible.module_utils.basic import *
from ansible.module_utils.ec2 import *

main()
