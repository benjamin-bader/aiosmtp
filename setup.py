import sys
from setuptools import setup, find_packages

install_requires = ['aiohttp']

tests_require = install_requires + ['nose', 'mocks']

setup(name='aiosmtp',
      version='0.0.1',
      description=('smtp server for asyncio'),
      long_description='TODO',
      classifiers=[
          'License :: OSI Approved :: Apache Software License',
          'Intended Audience :: Developers',
          'Programming Language :: Python',
          'Programming Language :: Python :: 3.3',
          'Programming Language :: Python :: 3.4',
          'Programming Language :: Python :: 3.5',
          'Topic :: Internet :: WWW/SMTP'],
      author='Ben Bader',
      author_email='pianoben@gmail.com',
      url='https://github.com/benjamin-bader/aiosmtp',
      packages=find_packages(),
      install_requires=install_requires,
      test_require=tests_require,
      test_suite='nose.collector',
      include_package_data=True)
