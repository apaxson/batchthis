Here will be placed various running log files based on the defined log levels

## Logging
Logging levels are defined in meadery/settings.py under Logging stanza:
```commandline
LOGGING = {
        ...
        'loggers': {
            'django': {
                'handlers': ['console'],
                'level': os.getenv('DJANGO_LOG_LEVEL', 'INFO'),
                'propagate': False,
            },
            'apps.batchthis': {
                'handlers': ['batchthis_file'],
                'level': 'DEBUG',
                'propogate': False,
                'formatter': 'verbose',
            },
            'apps.batchthis.fields': {
                'handlers': ['batchthis_file'],
                'level': 'DEBUG',
                'propogate': False,
                'formatter': 'verbose',
            },
      },
}
```