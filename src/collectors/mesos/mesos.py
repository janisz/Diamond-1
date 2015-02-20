# coding=utf-8

"""
Collect the Mesos stats for the local node.
"""

import urllib2

try:
    import json
except ImportError:
    import simplejson as json

import diamond.collector

from diamond.collector import str_to_bool


class MesosCollector(diamond.collector.Collector):
    def __init__(self, config=None, handlers=[], name=None, configfile=None):
        self.master = True
        self.known_frameworks = {}
        super(MesosCollector, self).__init__(config, handlers, name, configfile)

    def process_config(self):
        super(MesosCollector, self).process_config()
        self.master = str_to_bool(self.config['master'])

    def get_default_config_help(self):
        config_help = super(MesosCollector,
                            self).get_default_config_help()
        config_help.update({
            'host': "host running mesos",
            'port': "port on which the messos is listening",
            'master': "True if host is master."
        })
        return config_help

    def get_default_config(self):
        """
        Returns the default collector settings
        """
        config = super(MesosCollector, self).get_default_config()
        config.update({
            'host': '127.0.0.1',
            'port': 5050,
            'path': 'mesos',
            'master': True
        })
        return config

    def collect(self):
        if json is None:
            self.log.error('Unable to import json')
            return
        self._collect_metrics_snapshot()
        if not self.master:
            self._collect_slave_state()
            self._collect_slave_statistics()

    def _collect_metrics_snapshot(self):
        result = self._get(
            self.config['host'],
            self.config['port'],
            'metrics/snapshot'
        )
        if not result:
            return

        for key in result:
            value = result[key]
            self.publish(key, value, precision=self._precision(value))

    def _collect_slave_state(self):
        result = self._get(
            self.config['host'],
            self.config['port'],
            'slave(1)/state.json'
        )
        if not result:
            return

        for framework in result['frameworks']:
            self.known_frameworks[framework['id']] = framework['name']

        task_states = [
            'failed_tasks',
            'finished_tasks',
            'staged_tasks',
            'started_tasks',
            'lost_tasks'
        ]
        for key in task_states:
            value = result[key]
            self.publish(key, value, precision=self._precision(value))

    def _group_tasks_statistics(self, result):
        """This function groups statistics of same tasks by adding them.
        It also adds 'instances_count' statistic to get information about
        how many instances are running on the server

        Args:
            result: result of mesos query. List of dictionaries with
            'executor_id', 'framework_id' as strings and 'statistics'
            as a dictionary of labeled numbers
        Returns:
            Dictionary of dictionary with executor name as key (executor id
            reduced to task name without id) and statistics and framework id
        """
        for i in result:
            executor_id = i['executor_id']
            i['statistics']['instances_count'] = 1
            i['executor_id'] = executor_id[:executor_id.rfind('.')]
        r = {}
        for i in result:
            executor_id = i['executor_id']
            r[executor_id] = r.get(executor_id, {})
            r[executor_id]['framework_id'] = i['framework_id']
            r[executor_id]['statistics'] = r[executor_id].get('statistics', {})
            processed_result = r.get(executor_id, {'statistics': {}})
            grouped_statistics = processed_result['statistics']
            r[executor_id]['statistics'] = \
                self._sum_statistics(i['statistics'], grouped_statistics)
        return r

    def _sum_statistics(self, x, y):
        return {
            key: x.get(key, 0) + y.get(key, 0)
            for key in set(x) | set(y)
        }

    def _collect_slave_statistics(self):
        result = self._get(
            self.config['host'],
            self.config['port'],
            'monitor/statistics.json'
        )
        if not result:
            return

        result = self._group_tasks_statistics(result)

        for executor_id, executor in result.iteritems():
            executor_statistics = executor['statistics']
            for key in executor_statistics:
                value = executor_statistics[key]
                framework_id = self.known_frameworks[executor['framework_id']]
                framework = self._sanitize_metric_name(framework_id)
                executor_name = self._sanitize_metric_name(executor_id)
                metric = 'frameworks.%s.executors.%s.%s' % \
                         (framework, executor_name, key)
                self.publish(metric, value, precision=self._precision(value))

    def _get(self, host, port, path):
        """
        Execute a Mesos API call.
        """
        url = 'http://%s:%s/%s' % (host, port, path)
        try:
            response = urllib2.urlopen(url)
        except Exception, err:
            self.log.error("%s: %s", url, err)
            return False

        try:
            doc = json.load(response)
        except (TypeError, ValueError):
            self.log.error("Unable to parse response from Mesos as a"
                           + " json object")
            return False

        return doc

    def _precision(self, value):
        """
        Return the precision of the number
        """
        value = str(value)
        decimal = value.rfind('.')
        if decimal == -1:
            return 0
        return len(value) - decimal - 1

    def _sanitize_metric_name(self, name):
        return name.replace('.', '_').replace('/', '_')
