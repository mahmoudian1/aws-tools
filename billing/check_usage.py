#!/usr/bin/env python
# Copyright (c) 2014 Eugene Zhuk.
# Use of this source code is governed by the MIT license that can be found
# in the LICENSE file.

"""Checks AWS usage.

This script retrieves and displays an estimated total statement amount for
the specified billing period.

Usage:
    ./check_usage.py [options]
"""

import boto.cloudfront
import boto.ec2
import boto.iam
import boto.rds
import boto.route53
import boto.s3
import boto.sns
import boto.sdb
import boto.sqs
import boto.vpc
import csv
import itertools
import optparse
import re
import sys
import time


class Error(Exception):
    pass


def get_ec2_usage():
    ec2 = boto.connect_ec2()
    addrs = ec2.get_all_addresses()
    un = sum(a.instance_id is None for a in addrs)
    volumes = ec2.get_all_volumes()
    vs = sum(v.size for v in volumes)
    insts = list(itertools.chain.from_iterable([x.instances \
        for x in ec2.get_all_reservations()]))
    ic = len(insts)
    ir = sum(i.state_code == 16 for i in insts)
    print '{0} Elastic IP Addresse(s){1}\n' \
        '{2} Instance(s){3}\n' \
        '{4} Reserved Instance(s)\n' \
        '{5} Spot Instance Request(s)\n' \
        '{6} Volume(s){7}\n' \
        '{8} Snapshot(s)\n' \
        '{9} Image(s)\n' \
        '{10} Security Group(s)\n' \
        '{11} Key Pair(s)\n' \
        '{12} Network Interface(s)\n' \
        '{13} Tag(s)' \
        .format(len(addrs), ' [{0} unassigned]'.format(un) if 0 != un else '', \
            ic, ' [{0} running]'.format(ir) if ic != 0 else '', \
            len(ec2.get_all_reserved_instances()), \
            len(ec2.get_all_spot_instance_requests()), \
            len(volumes), ' [{0} GB]'.format(vs) if 0 != vs else '', \
            len(ec2.get_all_snapshots(owner=['self'])), \
            len(ec2.get_all_images(owners=['self'])), \
            len(ec2.get_all_security_groups()), \
            len(ec2.get_all_key_pairs()), \
            len(ec2.get_all_network_interfaces()), \
            len(ec2.get_all_tags()))


def get_cf_usage():
    cf = boto.connect_cloudfront()
    ds = cf.get_all_distributions()
    os = len(list(itertools.chain.from_iterable( \
        [x.get_distribution().get_objects() for x in ds])))
    print '{0} CloudFront Distribution(s){1}' \
        .format(len(ds), ' [{0} object(s)]'.format(os) if 0 != os else '')


def get_as_usage():
    autoscale = boto.connect_autoscale()
    gs = len(autoscale.get_all_groups())
    print '{0} Auto Scaling Group(s){1}\n' \
        '{2} Launch Configuration(s)\n' \
        '{3} Auto Scaling Policie(s)' \
        .format(gs, ' [{0} instances]' \
                .format(autoscale.get_all_autoscaling_instances()) \
                if 0 != gs else '', \
            len(autoscale.get_all_launch_configurations()), \
            len(autoscale.get_all_policies()))


def get_sns_usage():
    sns = boto.connect_sns()
    print '{0} Topic(s)\n' \
        '{1} Subscription(s)' \
        .format(len(sns.get_all_topics()), \
            len(sns.get_all_subscriptions()))


def get_sqs_usage():
    sqs = boto.connect_sqs()
    print '{0} Queue(s)'.format(len(sqs.get_all_queues()))


def get_cw_usage():
    cw = boto.connect_cloudwatch()
    print '{0} Alarm(s)'.format(len(cw.describe_alarms()))


def get_r53_usage():
    r53 = boto.connect_route53()
    zones = r53.get_zones()
    records = sum(len(z.get_records()) for z in zones)
    print '{0} Hosted Zone(s) [{1} record(s)]' \
        .format(len(zones), records)


def get_elb_usage():
    elb = boto.connect_elb()
    lbs = elb.get_all_load_balancers()
    ins = sum(len(x.instances) for x in lbs)
    print '{0} Elastic Load Balancer(s){1}' \
        .format(len(lbs), ' [{0} instance(s)]'.format(ins) \
            if 0 != ins else '')


def get_rds_usage():
    rds = boto.connect_rds()
    print '{0} RDS Instance(s)' \
        .format(len(rds.get_all_dbinstances()))


def get_vpc_usage():
    vpc = boto.connect_vpc()
    cgs = vpc.get_all_customer_gateways()
    igs = vpc.get_all_internet_gateways()
    print '{0} Virtual Private Cloud(s)\n' \
        '{1} Customer Gateway(s)\n' \
        '{2} Internet Gateway(s)\n' \
        '{3} Subnet(s)\n' \
        '{4} VPN Gateway(s)' \
        .format(len(vpc.get_all_vpcs()), \
            len(cgs), \
            len(igs), \
            len(vpc.get_all_subnets()), \
            len(vpc.get_all_vpn_gateways()))


def get_sdb_usage():
    sdb = boto.connect_sdb()
    print '{0} SimpleDB Domain(s)' \
        .format(len(sdb.get_all_domains()))


def get_iam_usage():
    iam = boto.connect_iam()
    us = iam.get_all_users()['list_users_response'] \
            ['list_users_result'] \
            ['users']
    gs = iam.get_all_groups()['list_groups_response'] \
            ['list_groups_result'] \
            ['groups']
    print '{0} IAM User(s)\n' \
        '{1} IAM Group(s)' \
        .format(len(us), \
            len(gs))


def get_s3_usage():
    s3 = boto.connect_s3()
    buckets = s3.get_all_buckets()
    res = sum([k.size for k in itertools.chain.from_iterable( \
        [b.get_all_keys() for b in buckets])])
    print '{0} S3 Bucket(s) [{1:.3f} GB]' \
        .format(len(buckets), res / float(1024 * 1024 * 1024))


def get_aws_cost(bucket_name, time_period):
    s3 = boto.connect_s3()
    bucket = s3.lookup(bucket_name)
    if bucket is None:
        raise Error('could not find \'{0}\''.format(bucket_name))

    period = time_period if time_period is not None \
        else time.strftime('%Y-%m', time.gmtime())

    data = ''
    for key in bucket.list():
        p = re.match(r'(\w+)-aws-billing-csv-{0}.csv' \
            .format(period), key.name)
        if p:
            data = key.get_contents_as_string()
            break
    if not data:
        raise Error('could not find billing data for this month')

    cost = dict()
    total = list()

    doc = csv.reader(data.rstrip('\n').split('\n'), delimiter=',')
    for row in doc:
        code = row[12]
        if code and code != 'ProductCode':
            value = float(row[28])
            if value >= 0:
                if not code in cost:
                    cost[code] = [row[13].split(' ', 1)[1], value, row[23]]
                else:
                    cost[code][1] += value
        if row[3] == 'StatementTotal':
            total.extend([['Cost', float(row[24]), row[23]], \
                ['Credit', float(row[25]), row[23]], \
                ['Total', float(row[28]), row[23]]])

    print '---'
    for k, v in cost.items():
        print '{0:<30} {1:>8.2f} {2}'.format(v[0], v[1], v[2])
    for v in total:
        print '{0:>29}: {1:>8.2f} {2}'.format(v[0], v[1], v[2])


def main():
    parser = optparse.OptionParser('Usage: %prog [options]')
    parser.add_option('-b', '--bucket', dest='bucket',
        help='The name of the S3 bucket that holds billing reports. This '
             'option is required.')
    parser.add_option('-p', '--period', dest='period',
        help='The billing period to check the usage for (e.g., \'2014-02\' '
             'without quotes). Defaults to the current billing period if '
             'not specified.')
    (opts, args) = parser.parse_args()

    if len(args) != 0 or \
       opts.bucket is None:
        parser.print_help()
        return 1

    try:
        get_ec2_usage()
        get_as_usage()
        get_sns_usage()
        get_sqs_usage()
        get_cw_usage()
        get_r53_usage()
        get_elb_usage()
        get_rds_usage()
        get_sdb_usage()
        get_cf_usage()
        get_vpc_usage()
        get_s3_usage()
        get_iam_usage()

        get_aws_cost(opts.bucket, opts.period)
    except (Error, Exception), err:
        sys.stderr.write('[ERROR] {0}\n'.format(err))
        return 1

    return 0


if __name__ == '__main__':
    sys.exit(main())
