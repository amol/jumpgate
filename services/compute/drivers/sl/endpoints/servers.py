import json
import falcon

from services.common.nested_dict import lookup
#from slapistack.utils.error_handling import bad_request
from SoftLayer import CCIManager
from SoftLayer.exceptions import SoftLayerAPIError

from core import api
from services.common.error_handling import bad_request, not_found
from services.compute import compute_dispatcher as disp

# This comes from Horizon. I wonder if there's a better place to get it.
POWER_STATES = {
    "NO STATE": 0,
    "RUNNING": 1,
    "BLOCKED": 2,
    "PAUSED": 3,
    "SHUTDOWN": 4,
    "SHUTOFF": 5,
    "CRASHED": 6,
    "SUSPENDED": 7,
    "FAILED": 8,
    "BUILDING": 9,
}

SERVER_STATUSES = [
    'ACTIVE', 'BUILD', 'DELETED', 'ERROR', 'HARD_REBOOT', 'PASSWORD', 'PAUSED',
    'REBOOT', 'REBUILD', 'RESCUE', 'RESIZE', 'REVERT_RESIZE', 'SHUTOFF',
    'SUSPENDED', 'UNKNOWN', 'VERIFY_RESIZE'
]


class SLComputeV2ServerAction(object):
    def on_post(self, req, resp, tenant_id, instance_id):
        body = json.loads(req.stream.read().decode())

        if len(body) == 0:
            return bad_request(resp, message="Malformed request body", code=400)

        client = api.config['sl_client']['Virtual_Guest']

        if 'pause' in body or 'suspend' in body:
            client.pause(id=instance_id)
            resp.status = falcon.HTTP_202
            return
        elif 'unpause' in body or 'resume' in body:
            client.resume(id=instance_id)
            resp.status = falcon.HTTP_202
            return
        elif 'reboot' in body:
            if body['reboot'].get('type') == 'SOFT':
                client.rebootSoft(id=instance_id)
            elif body['reboot'].get('type') == 'HARD':
                client.rebootHard(id=instance_id)
            else:
                client.rebootDefault(id=instance_id)
            resp.status = falcon.HTTP_202
            return
        elif 'os-stop' in body:
            client.powerOff(id=instance_id)
            resp.status = falcon.HTTP_202
            return
        elif 'os-start' in body:
            client.powerOn(id=instance_id)
            resp.status = falcon.HTTP_202
            return
        elif 'createImage' in body:
            template = {'name': body['createImage']['name'],
                        'volumes': body['createImage']['name']}
            client.captureImage(template, id=instance_id)
            resp.status = falcon.HTTP_202
            return
        elif 'os-getConsoleOutput' in body:
            resp.status = falcon.HTTP_501
            return

        return bad_request(resp,
                           message="There is no such action: %s" %
                           body.keys()[0], code=400)


class SLComputeV2Servers(object):
    def on_get(self, req, resp, tenant_id):
        client = api.config['sl_client']
        cci = CCIManager(client)

        params = {
            'mask': get_virtual_guest_mask(),
        }

        instances = cci.list_instances(**params)

        results = []

        for instance in instances:
            id = instance['id']
            results.append({
                'id': id,
                'links': [
                    {
                        'href': disp.get_endpoint_url('v2_server',
                                                      server_id=id),
                        'rel': 'self',
                    }
                ],
                'name': instance['hostname'],
            })

        resp.status = falcon.HTTP_200
        resp.body = json.dumps({'servers': results})

    def on_post(self, req, resp, tenant_id):
        body = json.loads(req.stream.read().decode())
        client = api.config['sl_client']
        cci = CCIManager(client)

        # TODO - Turn the flavor reference into actual numbers
        payload = {
            'hostname': body['server']['name'],
            'domain': 'example.com',  # TODO - Don't hardcode this
            'cpus': 2,
            'memory': 1024,
            'hourly': True,  # TODO - How do we set this accurately?
            # 'datacenter' => ['name' => $datacenter],
            'image_id': body['server']['imageRef'],
        }
        new_instance = cci.create_instance(**payload)

        print(new_instance)
#        result = cci.verify_create_instance(**payload)
#        new_instance = {'id': 2053839}

        resp.body = json.dumps({'server': {
            'id': new_instance['id'],
            'links': [{
                'href': disp.get_endpoint_url('v2_server',
                                              instance_id=new_instance['id']),
                'rel': 'self'}]
        }})


class SLComputeV2ServersDetail(object):
    def on_get(self, req, resp, tenant_id=None):
        client = api.config['sl_client']
        cci = CCIManager(client)

#        params = get_standard_params(['marker'])
        params = {}
        params['mask'] = get_virtual_guest_mask()

        # We're not using the API's pagination because of how Horizon's
        # pagination works.
        # TODO - Can I improve this performance by caching offsets and markers?
        # If I do, how will I deal with instance changes?
#        marker = params['marker']
#        del(params['marker'])
        marker = None

        results = []
        offset = 0

        if marker:
            for instance in cci.list_instances():
                if str(instance['id']) == marker:
                    break
                offset += 1

        if offset:
            params['offset'] = offset

        # TODO - REMOVE THIS
#        from SoftLayer.utils import query_filter
#        params['filter'] = {'virtualGuests': {
#            'hostname': query_filter('*nathan*')}}
#        params['limit'] = 5

        for instance in cci.list_instances(**params):
            results.append(get_server_details_dict(instance))

        resp.status = falcon.HTTP_200
        resp.body = json.dumps({'servers': results})


class SLComputeV2Server(object):
    def on_get(self, req, resp, tenant_id, server_id):
        client = api.config['sl_client']
        cci = CCIManager(client)

        params = {
            'id': server_id,
            'mask': get_virtual_guest_mask(),
        }

        try:
            instance = cci.get_instance(**params)
        except SoftLayerAPIError:
            return not_found(resp, 'Instance could not be found')

        results = get_server_details_dict(instance)

        resp.status = falcon.HTTP_200
        resp.body = json.dumps({'server': results})

    def delete(self, req, resp, tenant_id, instance_id):
        client = api.config['sl_client']
        cci = CCIManager(client)

        cci.cancel_instance(instance_id)
        resp.status = falcon.HTTP_204


def get_server_details_dict(instance):
    image_id = lookup(instance, 'blockDeviceTemplateGroup', 'globalIdentifier')
    tenant_id = instance['accountId']

    # TODO - Don't hardcode this flavor ID
    flavor_url = disp.get_endpoint_url('v2_flavor', flavor_id=1)
    server_url = disp.get_endpoint_url('v2_server', server_id=instance['id'])

    task_state = None
    transaction = lookup(instance, 'activeTransaction', 'transactionStatus',
                         'transaction_name')

    if 'CLOUD_INSTANCE_NETWORK_RECLAIM' == transaction:
        task_state = 'deleting'

    power_state = 0

    if instance['powerState']['keyName'] in POWER_STATES:
        power_state = POWER_STATES[instance['powerState']['keyName']]

    status = instance['powerState']['keyName']

    if 'RUNNING' == status:
        status = 'ACTIVE'
    elif 'HALTED' == status:
        status = 'SHUTOFF'
        power_state = POWER_STATES['SHUTDOWN']
    elif status not in SERVER_STATUSES and instance.get('provisionDate'):
        status = 'ACTIVE'

    addresses = {}
    if instance.get('primaryBackendIpAddress'):
        addresses['private'] = [{
            'addr': instance.get('primaryBackendIpAddress'),
            'version': 4,
        }]

    if instance.get('primaryIpAddress'):
        addresses['public'] = [{
            'addr': instance.get('primaryIpAddress'),
            'version': 4,
        }]

    # TODO - Don't hardcode this
    image_name = ''

    results = {
        'id': instance['id'],
        'accessIPv4': '',
        'accessIPv6': '',
        'addresses': addresses,
        'created': instance['createDate'],
        # TODO - Do I need to run this through isoformat()?
        'flavor': {
            # TODO - Make this realistic
            'id': '1',
            'links': [
                {
                    'href': flavor_url,
                    'rel': 'bookmark',
                },
            ],
        },
        'hostId': instance['id'],
        'links': [
            {
                'href': server_url,
                'rel': 'self',
            }
        ],
        'name': instance['hostname'],
        'OS-EXT-AZ:availability_zone': lookup(instance, 'datacenter', 'id'),
        'OS-EXT-STS:power_state': power_state,
        'OS-EXT-STS:task_state': task_state,
        'OS-EXT-STS:vm_state': instance['status']['keyName'],
        'security_groups': [{'name': 'default'}],
        'status': status,
        'tenant_id': tenant_id,
        'updated': instance['modifyDate'],
        'image_name': image_name,
    }

    if image_id:
        results['image'] = {
            'id': image_id,
            'links': [
                {
                    'href': disp.get_endpoint_url('v2_image',
                                                  image_id=image_id),
                    'rel': 'self',
                },
            ],
        }

    return results


def get_virtual_guest_mask():
    mask = [
        'id',
        'accountId',
        'hostname',
        'createDate',
        'blockDeviceTemplateGroup',
        'datacenter',
        'maxMemory',
        'maxCpu',
        'status',
        'powerState',
        'activeTransaction[transactionStatus]',
        'primaryIpAddress',
        'primaryBackendIpAddress',
        'modifyDate',
        'provisionDate',
    ]

    return 'mask[%s]' % ','.join(mask)
        