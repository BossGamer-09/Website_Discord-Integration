from celery import Celery, Task
from django.utils import timezone
from django_redis import get_redis_connection
from celery import shared_task
from celery.utils.log import get_task_logger
from django.core.cache import cache

from contextlib import ContextDecorator
from django.core.cache import caches, cache

from datetime import datetime
import functools

import hashlib
import json


PERIODIC_TASKS = dict()

celery_logger = get_task_logger(__name__)


class RegisterPeriodicTask(object): 
    def __init__(self, every, interval=None, name=None):
        from django_celery_beat.models import IntervalSchedule
        self.every = every
        self.interval = interval or IntervalSchedule.MINUTES
        self.name = name

    def __call__(self, func):
        path = '{0}.{1}'.format(func.__module__, func.__qualname__)
        PERIODIC_TASKS[path] = {"every":self.every, "interval":self.interval, "name":self.name}
        functools.update_wrapper(self, func)
        return func

register_periodic_task = RegisterPeriodicTask


class TaskLock(ContextDecorator):
    """
    Usable as both decorator and with statement,
    ensures task isnt ran more than once at the same time
    """
    def __init__(self, key=None, timeout=None, using=None, logger=celery_logger):
        self.key = key
        self.timeout = timeout
        self.logger = logger
        self.cache = caches[using] if using else cache
        self.acquired = False

    def __call__(self, func):
        self.func = func
        if self.key is None:
            self.key = '{0}.{1}:task_lock'.format(self.func.__module__, self.func.__qualname__)

        self.lock = cache.lock(self.key, timeout=self.timeout, thread_local=False, blocking_timeout=1)

        return super().__call__(self.func)

    def __enter__(self):
        self.acquired = self.lock.acquire(blocking=False)
        if not self.acquired:
            msg = "Celery Error: Duplicate locked task {} [{}] <<<<".format(self.key, datetime.now())
            self.logger.info(msg)
            raise RuntimeError(msg)
        return self

    def __exit__(self, type, value, traceback):
        if self.acquired:
            self.lock.release()

tasklock = TaskLock


# We subclass the Celery Task
class ExtendedCeleryTask(Task):
    def is_rate_okay(self, times=None, per=None, discriminator=None):
        """
            Checks to see if this task is hitting our defined rate limit too much.
            This example sets a rate limit of 30/minute.
            
            times (int): The "30" in "30 times per 60 seconds".
            per (int):  The "60" in "30 times per 60 seconds".
            
            The Redis structure we create is a Hash of timestamp keys with counter values
            {
                '1560649027.515933': '2',  // unlikely to have more than 1
                '1560649352.462433': '1',
            }
            
            The Redis key is expired after the amount of 'per' has elapsed.
            The algorithm totals the counters and checks against 'limit'.
            
            This algorithm currently does not implement the "leniency" described 
            at the bottom of the figma article referenced at the top of this code.
            This is left up to you and depends on application.
            
            Returns True if under the limit, otherwise False.
        """

        if times == None or per == None:
            return True

        redis_conn = get_redis_connection("default")

        # Get a timestamp accurate to the microsecond
        timestamp = timezone.now().timestamp()

        # Set our Redis key to our task name
        subkey = "-{}".format(discriminator) if discriminator != None else ""
        key = "rate:{}{}".format(self.name, subkey)

        # Create a pipeline to execute redis code atomically
        pipe = redis_conn.pipeline()

        # Increment our current task hit in the Redis hash
        pipe.hincrby(key, timestamp)

        # Grab the current expiration of our task key
        pipe.ttl(key)

        # Grab all of our task hits in our current frame (of 60 seconds)
        pipe.hvals(key)

        # This returns a list of our command results.  [current task hits, expiration, list of all task hits,]
        result = pipe.execute()

        # If our expiration is not set, set it.  This is not part of the atomicity of the pipeline above.
        if result[1] < 0:
            redis_conn.expire(key, per)

        # We must convert byte to int before adding up the counters and comparing to our limit
        if sum([int(count) for count in result[2]]) <= times:
            return True
        else:
            return False


class AlreadyQueued(Exception):
    """Raised when a task is already in the queue."""
    pass


class QueueOnce(Task):
    """
    A robust drop-in replacement for celery_once's QueueOnce.
    Prevents duplicate Celery tasks from being queued.
    """
    abstract = True
    once = {}

    def _get_lock_key(self, args, kwargs):
        """Generates a unique cache key based on task name and arguments."""
        def serialize(obj):
            try:
                # sort_keys ensures dicts with identical contents hash identically
                return json.dumps(obj, sort_keys=True)
            except TypeError:
                return str(obj)

        payload = f"{self.name}-{serialize(args)}-{serialize(kwargs)}"
        hashed_payload = hashlib.md5(payload.encode('utf-8')).hexdigest()
        return f"qo_{hashed_payload}"

    def apply_async(self, args=None, kwargs=None, **options):
        """Overrides apply_async to check for the lock before queuing."""
        args = args or ()
        kwargs = kwargs or {}
        
        once_config = getattr(self, 'once', {})
        graceful = once_config.get('graceful', False)
        timeout = once_config.get('timeout', 60 * 60) # Default: 1 hour

        key = self._get_lock_key(args, kwargs)

        # cache.add is ATOMIC. It returns True if the key was created, 
        # and False if it already exists.
        acquired = cache.add(key, "1", timeout)

        if not acquired:
            if graceful:
                return None
            raise AlreadyQueued(f"Task '{self.name}' with these arguments is already queued.")

        try:
            return super().apply_async(args, kwargs, **options)
        except Exception:
            # If the broker fails to accept the task, clear the lock so we aren't stuck
            cache.delete(key)
            raise

    def __call__(self, *args, **kwargs):
        """Handles the 'unlock_before_run' execution phase."""
        once_config = getattr(self, 'once', {})
        unlock_before_run = once_config.get('unlock_before_run', False)

        # self.request.id ensures we are executing in a Celery worker, 
        # not just being called synchronously in local code.
        if unlock_before_run and self.request.id:
            key = self._get_lock_key(args, kwargs)
            cache.delete(key)

        return super().__call__(*args, **kwargs)

    def after_return(self, status, retval, task_id, args, kwargs, einfo):
        """
        Safe cleanup phase. Called automatically by Celery after the task 
        finishes (success, failure, or ignored).
        """
        once_config = getattr(self, 'once', {})
        unlock_before_run = once_config.get('unlock_before_run', False)

        # If the task is retrying, DO NOT release the lock. 
        # We want to keep duplicates out until the retry succeeds or fails permanently.
        if status == 'RETRY':
            return super().after_return(status, retval, task_id, args, kwargs, einfo)

        # If we didn't unlock before running, release it now that the task is entirely done.
        if not unlock_before_run:
            key = self._get_lock_key(args, kwargs)
            cache.delete(key)

        super().after_return(status, retval, task_id, args, kwargs, einfo)
