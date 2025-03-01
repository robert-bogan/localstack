import datetime
import unittest
from urllib.parse import urlparse

import pytest
import pytz
from requests.models import Response

from localstack.aws.api import RequestContext
from localstack.constants import LOCALHOST, S3_VIRTUAL_HOSTNAME
from localstack.http import Request
from localstack.services.infra import patch_instance_tracker_meta
from localstack.services.s3 import (
    multipart_content,
    presigned_url,
    s3_listener,
    s3_starter,
    s3_utils,
)
from localstack.services.s3 import utils as s3_utils_asf
from localstack.services.s3.s3_utils import get_key_from_s3_url, get_s3_backend
from localstack.utils.strings import short_uid


class S3ListenerTest(unittest.TestCase):
    def test_expand_redirect_url(self):
        url1 = s3_listener.expand_redirect_url("http://example.org", "K", "B")
        self.assertEqual("http://example.org?key=K&bucket=B", url1)

        url2 = s3_listener.expand_redirect_url("http://example.org/?id=I", "K", "B")
        self.assertEqual("http://example.org/?id=I&key=K&bucket=B", url2)

    def test_find_multipart_key_value(self):
        headers = {
            "Host": "10.0.1.19:4572",
            "User-Agent": "curl/7.51.0",
            "Accept": "*/*",
            "Content-Length": "992",
            "Expect": "100-continue",
            "Content-Type": "multipart/form-data; boundary=------------------------3c48c744237517ac",
        }

        data1 = (
            b'--------------------------3c48c744237517ac\r\nContent-Disposition: form-data; name="key"\r\n\r\n'
            b"uploads/20170826T181315.679087009Z/upload/pixel.png\r\n--------------------------3c48c744237517ac"
            b'\r\nContent-Disposition: form-data; name="success_action_redirect"\r\n\r\nhttp://127.0.0.1:5000/'
            b"?id=20170826T181315.679087009Z\r\n--------------------------3c48c744237517ac--\r\n"
        )

        data2 = (
            b'--------------------------3c48c744237517ac\r\nContent-Disposition: form-data; name="key"\r\n\r\n'
            b"uploads/20170826T181315.679087009Z/upload/pixel.png\r\n--------------------------3c48c744237517ac"
            b"--\r\n"
        )

        data3 = (
            b'--------------------------3c48c744237517ac\r\nContent-Disposition: form-data; name="success_action_'
            b'redirect"\r\n\r\nhttp://127.0.0.1:5000/?id=20170826T181315.679087009Z\r\n--------------------------'
            b"3c48c744237517ac--\r\n"
        )

        data4 = (
            b'--------------------------3c48c744237517ac\r\nContent-Disposition: form-data; name="key"\r\n\r\n'
            b"uploads/20170826T181315.679087009Z/upload/pixel.png\r\n--------------------------3c48c744237517ac"
            b'\r\nContent-Disposition: form-data; name="success_action_status"\r\n\r\n201'
            b"\r\n--------------------------3c48c744237517ac--\r\n"
        )

        key1, url1 = multipart_content.find_multipart_key_value(data1, headers)

        self.assertEqual("uploads/20170826T181315.679087009Z/upload/pixel.png", key1)
        self.assertEqual("http://127.0.0.1:5000/?id=20170826T181315.679087009Z", url1)

        key2, url2 = multipart_content.find_multipart_key_value(data2, headers)

        self.assertEqual("uploads/20170826T181315.679087009Z/upload/pixel.png", key2)
        self.assertIsNone(url2, "Should not get a redirect URL without success_action_redirect")

        key3, url3 = multipart_content.find_multipart_key_value(data3, headers)

        self.assertIsNone(key3, "Should not get a key without provided key")
        self.assertIsNone(url3, "Should not get a redirect URL without provided key")

        key4, status_code = multipart_content.find_multipart_key_value(
            data4, headers, "success_action_status"
        )

        self.assertEqual("uploads/20170826T181315.679087009Z/upload/pixel.png", key4)
        self.assertEqual("201", status_code)

    def test_expand_multipart_filename(self):
        headers = {
            "Host": "10.0.1.19:4572",
            "User-Agent": "curl/7.51.0",
            "Accept": "*/*",
            "Content-Length": "992",
            "Expect": "100-continue",
            "Content-Type": "multipart/form-data; boundary=------------------------3c48c744237517ac",
        }

        data1 = (
            b'--------------------------3c48c744237517ac\r\nContent-Disposition: form-data; name="key"\r\n\r\n'
            b"uploads/20170826T181315.679087009Z/upload/${filename}\r\n--------------------------3c48c744237517ac"
            b'\r\nContent-Disposition: form-data; name="AWSAccessKeyId"\r\n\r\nWHAT\r\n--------------------------'
            b'3c48c744237517ac\r\nContent-Disposition: form-data; name="policy"\r\n\r\nNO\r\n--------------------'
            b'------3c48c744237517ac\r\nContent-Disposition: form-data; name="signature"\r\n\r\nYUP\r\n----------'
            b'----------------3c48c744237517ac\r\nContent-Disposition: form-data; name="acl"\r\n\r\nprivate\r\n--'
            b'------------------------3c48c744237517ac\r\nContent-Disposition: form-data; name="success_action_re'
            b'direct"\r\n\r\nhttp://127.0.0.1:5000/\r\n--------------------------3c48c744237517ac\r\nContent-Disp'
            b'osition: form-data; name="file"; filename="pixel.png"\r\nContent-Type: application/octet-stream\r\n'
            b"\r\n\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15"
            b"\xc4\x89\x00\x00\x00\x19tEXtSoftware\x00Adobe ImageReadyq\xc9e<\x00\x00\x00\x0eIDATx\xdabb\x00\x02"
            b"\x80\x00\x03\x00\x00\x0f\x00\x03`|\xce\xe9\x00\x00\x00\x00IEND\xaeB`\x82\r\n-----------------------"
            b"---3c48c744237517ac--\r\n"
        )

        data2 = (
            b'--------------------------3c48c744237517ac\r\nContent-Disposition: form-data; name="key"\r\n\r\n'
            b"uploads/20170826T181315.679087009Z/upload/pixel.png\r\n--------------------------3c48c744237517ac"
            b'\r\nContent-Disposition: form-data; name="AWSAccessKeyId"\r\n\r\nWHAT\r\n--------------------------'
            b'3c48c744237517ac\r\nContent-Disposition: form-data; name="policy"\r\n\r\nNO\r\n--------------------'
            b'------3c48c744237517ac\r\nContent-Disposition: form-data; name="signature"\r\n\r\nYUP\r\n----------'
            b'----------------3c48c744237517ac\r\nContent-Disposition: form-data; name="acl"\r\n\r\nprivate\r\n--'
            b'------------------------3c48c744237517ac\r\nContent-Disposition: form-data; name="success_action_re'
            b'direct"\r\n\r\nhttp://127.0.0.1:5000/\r\n--------------------------3c48c744237517ac\r\nContent-Disp'
            b'osition: form-data; name="file"; filename="pixel.png"\r\nContent-Type: application/octet-stream\r\n'
            b"\r\n\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15"
            b"\xc4\x89\x00\x00\x00\x19tEXtSoftware\x00Adobe ImageReadyq\xc9e<\x00\x00\x00\x0eIDATx\xdabb\x00\x02"
            b"\x80\x00\x03\x00\x00\x0f\x00\x03`|\xce\xe9\x00\x00\x00\x00IEND\xaeB`\x82\r\n-----------------------"
            b"---3c48c744237517ac--\r\n"
        )

        data3 = (
            '--------------------------3c48c744237517ac\r\nContent-Disposition: form-data; name="key"\r\n\r\n'
            "uploads/20170826T181315.679087009Z/upload/${filename}\r\n--------------------------3c48c744237517ac"
            '\r\nContent-Disposition: form-data; name="AWSAccessKeyId"\r\n\r\nWHAT\r\n--------------------------'
            '3c48c744237517ac\r\nContent-Disposition: form-data; name="policy"\r\n\r\nNO\r\n--------------------'
            '------3c48c744237517ac\r\nContent-Disposition: form-data; name="signature"\r\n\r\nYUP\r\n----------'
            '----------------3c48c744237517ac\r\nContent-Disposition: form-data; name="acl"\r\n\r\nprivate\r\n--'
            '------------------------3c48c744237517ac\r\nContent-Disposition: form-data; name="success_action_re'
            'direct"\r\n\r\nhttp://127.0.0.1:5000/\r\n--------------------------3c48c744237517ac\r\nContent-Disp'
            'osition: form-data; name="file"; filename="pixel.txt"\r\nContent-Type: text/plain\r\n\r\nHello World'
            "\r\n--------------------------3c48c744237517ac--\r\n"
        )

        expanded1 = multipart_content.expand_multipart_filename(data1, headers)
        self.assertIsNot(
            expanded1,
            data1,
            "Should have changed content of data with filename to interpolate",
        )
        self.assertIn(
            b"uploads/20170826T181315.679087009Z/upload/pixel.png",
            expanded1,
            "Should see the interpolated filename",
        )

        expanded2 = multipart_content.expand_multipart_filename(data2, headers)
        self.assertIs(
            expanded2,
            data2,
            "Should not have changed content of data with no filename to interpolate",
        )

        expanded3 = multipart_content.expand_multipart_filename(data3, headers)
        self.assertIsNot(
            expanded3,
            data3,
            "Should have changed content of string data with filename to interpolate",
        )
        self.assertIn(
            b"uploads/20170826T181315.679087009Z/upload/pixel.txt",
            expanded3,
            "Should see the interpolated filename",
        )

    def test_event_type_matching(self):
        match = s3_listener.event_type_matches
        self.assertTrue(match(["s3:ObjectCreated:*"], "ObjectCreated", "Put"))
        self.assertTrue(match(["s3:ObjectCreated:*"], "ObjectCreated", "Post"))
        self.assertTrue(match(["s3:ObjectCreated:Post"], "ObjectCreated", "Post"))
        self.assertTrue(match(["s3:ObjectDeleted:*"], "ObjectDeleted", "Delete"))
        self.assertFalse(match(["s3:ObjectCreated:Post"], "ObjectCreated", "Put"))
        self.assertFalse(match(["s3:ObjectCreated:Post"], "ObjectDeleted", "Put"))

    def test_is_query_allowable(self):
        self.assertTrue(s3_listener.ProxyListenerS3.is_query_allowable("POST", "uploadId"))
        self.assertTrue(s3_listener.ProxyListenerS3.is_query_allowable("POST", ""))
        self.assertTrue(s3_listener.ProxyListenerS3.is_query_allowable("PUT", ""))
        self.assertFalse(
            s3_listener.ProxyListenerS3.is_query_allowable("POST", "differentQueryString")
        )
        # abort multipart upload is a delete with the same query string as a complete multipart upload
        self.assertFalse(s3_listener.ProxyListenerS3.is_query_allowable("DELETE", "uploadId"))
        self.assertFalse(
            s3_listener.ProxyListenerS3.is_query_allowable("DELETE", "differentQueryString")
        )
        self.assertFalse(s3_listener.ProxyListenerS3.is_query_allowable("PUT", "uploadId"))

    def test_append_last_modified_headers(self):
        xml_with_last_modified = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">'
            "  <Name>thanos/Name>"
            "  <Contents>"
            "    <LastModified>2019-05-27T19:00:16.663Z</LastModified>"
            "  </Contents>"
            "</ListBucketResult>"
        )
        xml_without_last_modified = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">'
            "  <Name>thanos/Name>"
            "  <Contents>"
            "    <NotLastModified>2019-05-27T19:00:16.663Z</NotLastModified>"
            "  </Contents>"
            "</ListBucketResult>"
        )

        # if there is a parsable date in XML <LastModified>, use it
        response = Response()
        s3_listener.append_last_modified_headers(response, content=xml_with_last_modified)
        self.assertEqual("Mon, 27 May 2019 19:00:16 GMT", response.headers.get("Last-Modified", ""))

        # otherwise, just fill the header with the currentdate
        # I will not test currentDate as it is not trivial without adding dependencies
        # so, I'm testing for the presence of the header only
        response = Response()
        s3_listener.append_last_modified_headers(response, content=xml_without_last_modified)
        self.assertNotEqual("No header", response.headers.get("Last-Modified", "No header"))

        response = Response()
        s3_listener.append_last_modified_headers(response)
        self.assertNotEqual("No header", response.headers.get("Last-Modified", "No header"))


class TestS3Utils:
    def test_s3_bucket_name(self):
        # array description : 'bucket_name', 'expected_ouput'
        bucket_names = [
            ("docexamplebucket1", True),
            ("log-delivery-march-2020", True),
            ("my-hosted-content", True),
            ("docexamplewebsite.com", True),
            ("www.docexamplewebsite.com", True),
            ("my.example.s3.bucket", True),
            ("doc_example_bucket", False),
            ("DocExampleBucket", False),
            ("doc-example-bucket-", False),
        ]

        for bucket_name, expected_result in bucket_names:
            assert s3_utils.validate_bucket_name(bucket_name) == expected_result

    def test_is_expired(self):
        offset = datetime.timedelta(seconds=5)
        assert s3_utils.is_expired(datetime.datetime.now() - offset)
        assert not s3_utils.is_expired(datetime.datetime.now() + offset)

    def test_is_expired_with_tz(self):
        offset = datetime.timedelta(seconds=5)
        assert s3_utils.is_expired(datetime.datetime.now(tz=pytz.timezone("EST")) - offset)
        assert not s3_utils.is_expired(datetime.datetime.now(tz=pytz.timezone("EST")) + offset)

    def test_bucket_name(self):
        # array description : 'path', 'header', 'expected_ouput'
        bucket_names = [
            ("/bucket/keyname", {"host": f"https://{LOCALHOST}:4566"}, "bucket"),
            ("/bucket//keyname", {"host": f"https://{LOCALHOST}:4566"}, "bucket"),
            ("/keyname", {"host": f"bucket.{S3_VIRTUAL_HOSTNAME}:4566"}, "bucket"),
            ("//keyname", {"host": f"bucket.{S3_VIRTUAL_HOSTNAME}:4566"}, "bucket"),
            ("/", {"host": f"{S3_VIRTUAL_HOSTNAME}:4566"}, None),
            ("/", {"host": "bucket.s3-ap-northeast-1.amazonaws.com:4566"}, "bucket"),
            ("/", {"host": "bucket.s3-ap-northeast-2.amazonaws.com:4566"}, "bucket"),
            ("/", {"host": "bucket.s3-ap-south-1.amazonaws.com"}, "bucket"),
            ("/", {"host": "bucket.s3-ap-southeast-1.amazonaws.com"}, "bucket"),
            ("/", {"host": "bucket.s3-ap-southeast-2.amazonaws.com"}, "bucket"),
            ("/", {"host": "bucket.s3-ca-central-1.amazonaws.com"}, "bucket"),
            ("/", {"host": "bucket.s3-eu-central-1.amazonaws.com"}, "bucket"),
            ("/", {"host": "http://bucket.s3-eu-west-1.amazonaws.com"}, "bucket"),
            ("/", {"host": "http://bucket.s3-eu-west-2.amazonaws.com"}, "bucket"),
            ("/", {"host": "http://bucket.s3-eu-west-3.amazonaws.com"}, "bucket"),
            ("/", {"host": "http://bucket.s3-external-1.amazonaws.com"}, "bucket"),
            ("/", {"host": "http://bucket.s3-sa-east-1.amazonaws.com"}, "bucket"),
            ("/", {"host": "bucket.s3-us-east-2.amazonaws.com"}, "bucket"),
            ("/", {"host": "bucket.s3-us-west-1.amazonaws.com"}, "bucket"),
            ("/", {"host": "bucket.s3-us-west-2.amazonaws.com"}, "bucket"),
            ("/", {"host": "bucket.s3.amazonaws.com"}, "bucket"),
            ("/", {"host": "bucket.s3.ap-northeast-1.amazonaws.com"}, "bucket"),
            ("/", {"host": "bucket.s3.ap-northeast-2.amazonaws.com"}, "bucket"),
            ("/", {"host": "bucket.s3.ap-south-1.amazonaws.com"}, "bucket"),
            ("/", {"host": "bucket.s3.ap-southeast-1.amazonaws.com"}, "bucket"),
            ("/", {"host": "bucket.s3.ap-southeast-2.amazonaws.com"}, "bucket"),
            ("/", {"host": "bucket.s3.ca-central-1.amazonaws.com"}, "bucket"),
            ("/", {"host": "bucket.s3.cn-north-1.amazonaws.com.cn"}, "bucket"),
            ("/", {"host": "bucket.s3.cn-northwest-1.amazonaws.com.cn"}, "bucket"),
            (
                "/",
                {"host": "bucket.s3.dualstack.ap-northeast-1.amazonaws.com"},
                "bucket",
            ),
            (
                "/",
                {"host": "https://bucket.s3.dualstack.ap-northeast-2.amazonaws.com"},
                "bucket",
            ),
            (
                "/",
                {"host": "https://bucket.s3.dualstack.ap-south-1.amazonaws.com"},
                "bucket",
            ),
            (
                "/",
                {"host": "https://bucket.s3.dualstack.ap-southeast-1.amazonaws.com"},
                "bucket",
            ),
            (
                "/",
                {"host": "https://bucket.s3.dualstack.ap-southeast-2.amazonaws.com"},
                "bucket",
            ),
            (
                "/",
                {"host": "https://bucket.s3.dualstack.ca-central-1.amazonaws.com"},
                "bucket",
            ),
            (
                "/",
                {"host": "https://bucket.s3.dualstack.eu-central-1.amazonaws.com"},
                "bucket",
            ),
            ("/", {"host": "bucket.s3.dualstack.eu-west-1.amazonaws.com"}, "bucket"),
            ("/", {"host": "bucket.s3.dualstack.eu-west-2.amazonaws.com"}, "bucket"),
            ("/", {"host": "bucket.s3.dualstack.eu-west-3.amazonaws.com"}, "bucket"),
            ("/", {"host": "bucket.s3.dualstack.sa-east-1.amazonaws.com"}, "bucket"),
            ("/", {"host": "bucket.s3.dualstack.us-east-1.amazonaws.com"}, "bucket"),
            ("/", {"host": "bucket.s3.dualstack.us-east-2.amazonaws.com"}, "bucket"),
            ("/", {"host": "bucket.s3.dualstack.us-west-1.amazonaws.com"}, "bucket"),
            ("/", {"host": "bucket.s3.dualstack.us-west-2.amazonaws.com"}, "bucket"),
            ("/", {"host": "bucket.s3.eu-central-1.amazonaws.com"}, "bucket"),
            ("/", {"host": "bucket.s3.eu-west-1.amazonaws.com"}, "bucket"),
            ("/", {"host": "bucket.s3.eu-west-2.amazonaws.com"}, "bucket"),
            ("/", {"host": "bucket.s3.eu-west-3.amazonaws.com"}, "bucket"),
            ("/", {"host": "bucket.s3.sa-east-1.amazonaws.com"}, "bucket"),
            ("/", {"host": "bucket.s3.us-east-1.amazonaws.com"}, "bucket"),
            ("/", {"host": "bucket.s3.us-east-2.amazonaws.com"}, "bucket"),
            ("/", {"host": "bucket.s3.us-west-1.amazonaws.com"}, "bucket"),
            ("/", {"host": "bucket.s3.us-west-2.amazonaws.com"}, "bucket"),
            ("/", {"host": "bucket.s3.localhost.localstack.cloud"}, "bucket"),
            (
                "/",
                {"host": "bucket-1.s3-website.localhost.localstack.cloud"},
                "bucket-1",
            ),
            (
                "/",
                {"host": "bucket.localhost.localstack.cloud"},
                "bucket",
            ),  # internally agreed upon special case
            ("/", {"host": "localhost.localstack.cloud"}, None),
            ("/", {"host": "test.dynamodb.amazonaws.com"}, None),
            ("/", {"host": "dynamodb.amazonaws.com"}, None),
        ]

        for path, headers, expected_result in bucket_names:
            assert s3_utils.extract_bucket_name(headers, path) == expected_result

    # test whether method correctly distinguishes between hosted and path style bucket references
    # path style format example: https://s3.{region}.localhost.localstack.cloud:4566/{bucket-name}/{key-name}
    # hosted style format example: http://aws.s3.localhost.localstack.cloud:4566/
    def test_uses_host_address(self):
        addresses = [
            ({"host": f"https://aws.{LOCALHOST}:4566"}, False),
            # attention: This is **not** a host style reference according to s3 specs but a special case from our side
            ({"host": f"https://aws.{LOCALHOST}.localstack.cloud:4566"}, True),
            ({"host": f"https://{LOCALHOST}.aws:4566"}, False),
            ({"host": f"https://{LOCALHOST}.swa:4566"}, False),
            ({"host": f"https://swa.{LOCALHOST}:4566"}, False),
            ({"host": "https://bucket.s3.localhost.localstack.cloud"}, True),
            ({"host": "bucket.s3.eu-west-1.amazonaws.com"}, True),
            ({"host": "https://s3.eu-west-1.localhost.localstack.cloud/bucket"}, False),
            ({"host": "https://s3.eu-west-1.localhost.localstack.cloud/bucket/key"}, False),
            ({"host": "https://s3.localhost.localstack.cloud/bucket"}, False),
            ({"host": "https://bucket.s3.eu-west-1.localhost.localstack.cloud/key"}, True),
            (
                {
                    "host": "https://bucket.s3.eu-west-1.localhost.localstack.cloud/key/key/content.png"
                },
                True,
            ),
            ({"host": "https://s3.localhost.localstack.cloud/bucket/key"}, False),
            ({"host": "https://bucket.s3.eu-west-1.localhost.localstack.cloud"}, True),
            ({"host": "https://bucket.s3.localhost.localstack.cloud/key"}, True),
            ({"host": "bucket.s3.eu-west-1.amazonaws.com"}, True),
            ({"host": "bucket.s3.amazonaws.com"}, True),
            ({"host": "notabucket.amazonaws.com"}, False),
            ({"host": "s3.amazonaws.com"}, False),
            ({"host": "s3.eu-west-1.amazonaws.com"}, False),
        ]
        for headers, expected_result in addresses:
            assert s3_utils.uses_host_addressing(headers) == expected_result

    def test_s3_keyname_name(self):
        # array description : 'path', 'header', 'expected_ouput'
        key_names = [
            ("/bucket/keyname", {"host": f"https://{LOCALHOST}:4566"}, "keyname"),
            ("/bucket//keyname", {"host": f"https://{LOCALHOST}:4566"}, "/keyname"),
            (
                "/keyname",
                {"host": f"https://bucket.{S3_VIRTUAL_HOSTNAME}:4566"},
                "keyname",
            ),
            (
                "//keyname",
                {"host": f"https://bucket.{S3_VIRTUAL_HOSTNAME}:4566"},
                "/keyname",
            ),
        ]

        for path, headers, expected_result in key_names:
            assert s3_utils.extract_key_name(headers, path) == expected_result

    def test_get_key_from_s3_url(self):
        for prefix in ["s3://test-bucket/", "", "/"]:
            for slash_prefix in [True, False]:
                for key in ["my/key/123", "/mykey"]:
                    url = f"{prefix}{key}"
                    expected = f"{'/' if slash_prefix else ''}{key.lstrip('/')}"
                    assert get_key_from_s3_url(url, leading_slash=slash_prefix) == expected


class S3BackendTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        s3_starter.apply_patches()
        patch_instance_tracker_meta()

    def test_key_instances_before_removing(self):
        s3_backend = get_s3_backend()

        bucket_name = "test"
        region = "us-east-1"

        file1_name = "file.txt"
        file2_name = "file2.txt"
        file_value = b"content"

        s3_backend.create_bucket(bucket_name, region)
        s3_backend.put_object(bucket_name, file1_name, file_value)
        s3_backend.put_object(bucket_name, file2_name, file_value)

        key = s3_backend.get_object(bucket_name, file2_name)

        self.assertNotIn(key, key.instances or [])

    def test_no_bucket_in_instances(self):
        s3_backend = get_s3_backend()

        bucket_name = f"b-{short_uid()}"
        region = "us-east-1"

        s3_backend.create_bucket(bucket_name, region)

        s3_backend.delete_bucket(bucket_name)
        bucket = s3_backend.create_bucket(bucket_name, region)

        self.assertNotIn(bucket, (bucket.instances or []))


class TestS3UtilsAsf:
    """
    Testing the new utils from ASF
    Some utils are duplicated, but it will be easier once we remove the old listener, we won't have to
    untangle and leave old functions around
    """

    # test whether method correctly distinguishes between hosted and path style bucket references
    # path style format example: https://s3.{region}.localhost.localstack.cloud:4566/{bucket-name}/{key-name}
    # hosted style format example: http://{bucket-name}.s3.{region}localhost.localstack.cloud:4566/
    # region is optional in localstack
    # the requested has been forwarded by the router, and S3_VIRTUAL_HOST_FORWARDED_HEADER has been added with the
    # original host header
    def test_uses_host_address(self):
        host_header = s3_utils_asf.S3_VIRTUAL_HOST_FORWARDED_HEADER
        addresses = [
            ({host_header: f"https://aws.{LOCALHOST}:4566"}, False),
            # attention: This is **not** a host style reference according to s3 specs but a special case from our side
            ({host_header: f"https://aws.{LOCALHOST}.localstack.cloud:4566"}, True),
            ({host_header: f"https://{LOCALHOST}.aws:4566"}, False),
            ({host_header: f"https://{LOCALHOST}.swa:4566"}, False),
            ({host_header: f"https://swa.{LOCALHOST}:4566"}, False),
            ({host_header: "https://bucket.s3.localhost.localstack.cloud"}, True),
            ({host_header: "bucket.s3.eu-west-1.amazonaws.com"}, True),
            ({host_header: "https://s3.eu-west-1.localhost.localstack.cloud/bucket"}, False),
            ({host_header: "https://s3.eu-west-1.localhost.localstack.cloud/bucket/key"}, False),
            ({host_header: "https://s3.localhost.localstack.cloud/bucket"}, False),
            ({host_header: "https://bucket.s3.eu-west-1.localhost.localstack.cloud/key"}, True),
            (
                {
                    host_header: "https://bucket.s3.eu-west-1.localhost.localstack.cloud/key/key/content.png"
                },
                True,
            ),
            ({host_header: "https://s3.localhost.localstack.cloud/bucket/key"}, False),
            ({host_header: "https://bucket.s3.eu-west-1.localhost.localstack.cloud"}, True),
            ({host_header: "https://bucket.s3.localhost.localstack.cloud/key"}, True),
            ({host_header: "bucket.s3.eu-west-1.amazonaws.com"}, True),
            ({host_header: "bucket.s3.amazonaws.com"}, True),
            ({host_header: "notabucket.amazonaws.com"}, False),
            ({host_header: "s3.amazonaws.com"}, False),
            ({host_header: "s3.eu-west-1.amazonaws.com"}, False),
        ]
        for headers, expected_result in addresses:
            assert s3_utils_asf.uses_host_addressing(headers) == expected_result

    def test_is_valid_canonical_id(self):
        canonical_ids = [
            (
                "0f84b30102b8e116121884e982fedc9d76715877fc810605f7ba5dca143b3bb0",
                True,
            ),  # 64 len hex string
            ("f945fc46e86d3af9b2ebf8bda159f94b8f6be81413a5a2e21e8fd3a059de55a9", True),
            ("73E7AFD3413526244BDA3D3E08CF191115773EFF5D875B4860963A71AB7C13E6", True),
            ("0f84b30102b8e116121884e982fedc9d76715877fc810605f7ba5dca143b3bb", False),
            ("0f84b30102b8e116121884e982fedc9d76715877fc810605f7ba5dca143b3bb00", False),
            ("0f84b30102b8e116121884e982fedc9d76715877fc810605f7ba5dca143b3bbz", False),
            ("KXy1MCaCAUmbwQGOqVkJrzIDEbDPg4mLwMMzj8CyFdmbZx-JAm158soGrLlPZwXG", False),
            ("KXy1MCaCAUmbwQGOqVkJrzIDEbDPg4mLwMMzj8CyFdmbZx", False),
        ]
        for canonical_id, expected_result in canonical_ids:
            assert s3_utils_asf.is_valid_canonical_id(canonical_id) == expected_result

    def test_get_header_name(self):
        """
        Test to transform shape member names into their header location
        We could maybe use the specs for this
        """
        query_params = [
            ("GrantFullControl", "x-amz-grant-full-control"),
            ("GrantRead", "x-amz-grant-read"),
            ("GrantReadACP", "x-amz-grant-read-acp"),
            ("GrantWrite", "x-amz-grant-write"),
            ("GrantWriteACP", "x-amz-grant-write-acp"),
        ]

        for query_param, expected_header_name in query_params:
            assert s3_utils_asf.get_header_name(query_param) == expected_header_name

    def test_is_canned_acl_valid(self):
        canned_acls = [
            ("private", True),
            ("public-read", True),
            ("public-read-write", True),
            ("authenticated-read", True),
            ("aws-exec-read", True),
            ("bucket-owner-read", True),
            ("bucket-owner-full-control", True),
            ("not-a-canned-one", False),
            ("aws--exec-read", False),
            ("log-delivery-write", True),
        ]

        for canned_acl, expected_result in canned_acls:
            assert s3_utils_asf.is_canned_acl_bucket_valid(canned_acl) == expected_result

    def test_s3_bucket_name(self):
        bucket_names = [
            ("docexamplebucket1", True),
            ("log-delivery-march-2020", True),
            ("my-hosted-content", True),
            ("docexamplewebsite.com", True),
            ("www.docexamplewebsite.com", True),
            ("my.example.s3.bucket", True),
            ("doc_example_bucket", False),
            ("DocExampleBucket", False),
            ("doc-example-bucket-", False),
        ]

        for bucket_name, expected_result in bucket_names:
            assert s3_utils_asf.is_bucket_name_valid(bucket_name) == expected_result

    def test_verify_checksum(self):
        valid_checksums = [
            (
                "SHA256",
                b"test data..",
                {"ChecksumSHA256": "2l26x0trnT0r2AvakoFk2MB7eKVKzYESLMxSAKAzoik="},
            ),
            ("CRC32", b"test data..", {"ChecksumCRC32": "cZWHwQ=="}),
            ("CRC32C", b"test data..", {"ChecksumCRC32C": "Pf4upw=="}),
            ("SHA1", b"test data..", {"ChecksumSHA1": "B++3uSfJMSHWToQMQ1g6lIJY5Eo="}),
            (
                "SHA1",
                b"test data..",
                {"ChecksumSHA1": "B++3uSfJMSHWToQMQ1g6lIJY5Eo=", "ChecksumCRC32C": "test"},
            ),
        ]

        for checksum_algorithm, data, request in valid_checksums:
            # means that it did not raise an exception
            assert s3_utils_asf.verify_checksum(checksum_algorithm, data, request) is None

        invalid_checksums = [
            (
                "sha256&",
                b"test data..",
                {"ChecksumSHA256": "2l26x0trnT0r2AvakoFk2MB7eKVKzYESLMxSAKAzoik="},
            ),
            (
                "sha256",
                b"test data..",
                {"ChecksumSHA256": "2l26x0trnT0r2AvakoFk2MB7eKVKzYESLMxSAKAzoik="},
            ),
            ("CRC32", b"test data..", {"ChecksumCRC32": "cZWHwQ==="}),
            ("CRC32", b"test data.", {"ChecksumCRC32C": "Pf4upw=="}),
            ("SHA1", b"test da\nta..", {"ChecksumSHA1": "B++3uSfJMSHWToQMQ1g6lIJY5Eo="}),
        ]
        for checksum_algorithm, data, request in invalid_checksums:
            with pytest.raises(Exception):
                s3_utils_asf.verify_checksum(checksum_algorithm, data, request)


class TestS3PresignedUrlAsf:
    """
    Testing utils from the new Presigned URL validation with ASF
    """

    @staticmethod
    def _create_fake_context_from_path(path: str, method: str = "GET"):
        fake_context = RequestContext()
        fake_context.request = Request(
            method=method, path=path, query_string=urlparse(f"http://localhost{path}").query
        )
        return fake_context

    def test_is_presigned_url_request(self):
        request_paths = [
            (
                "GET",
                "/?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Credential=test&X-Amz-Date=test&X-Amz-Expires=test&X-Amz-SignedHeaders=host&X-Amz-Signature=test",
                True,
            ),
            (
                "PUT",
                "/?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Credential=test&X-Amz-Date=test&X-Amz-Expires=test&X-Amz-SignedHeaders=host&X-Amz-Signature=test",
                True,
            ),
            (
                "GET",
                "/?acl&X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Credential=test&X-Amz-Date=test&X-Amz-Expires=test&X-Amz-SignedHeaders=host&X-Amz-Signature=test",
                True,
            ),
            (
                "GET",
                "/?acl&X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Date=test&X-Amz-Expires=test&X-Amz-SignedHeaders=host&X-Amz-Signature=test",
                True,
            ),
            (
                "GET",
                "/?acl&X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Credential=test&X-Amz-Date=testX-Amz-Expires=test&X-Amz-SignedHeaders=host",
                True,
            ),
            (
                "GET",
                "/?X-Amz-Credential=test&X-Amz-Date=testX-Amz-Expires=test&X-Amz-SignedHeaders=host&X-Amz-Signature=test",
                True,
            ),
            ("GET", "/?AWSAccessKeyId=test&Signature=test&Expires=test", True),
            ("GET", "/?acl&AWSAccessKeyId=test&Signature=test&Expires=test", True),
            ("GET", "/?acl&AWSAccessKey=test", False),
            ("GET", "/?acl", False),
            (
                "GET",
                "/?x-Amz-Credential=test&x-Amz-Date=testx-Amz-Expires=test&x-Amz-SignedHeaders=host&x-Amz-Signature=test",
                False,
            ),
        ]

        for method, request_path, expected_result in request_paths:
            fake_context = self._create_fake_context_from_path(path=request_path, method=method)
            assert (
                presigned_url.is_presigned_url_request(fake_context) == expected_result
            ), request_path

    def test_is_valid_presigned_url_v2(self):
        # structure: method, path, is_sig_v2, will_raise
        request_paths = [
            (
                "GET",
                "/?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Credential=test&X-Amz-Date=test&X-Amz-Expires=test&X-Amz-SignedHeaders=host&X-Amz-Signature=test",
                False,
                False,
            ),
            ("GET", "/?acl", False, False),
            ("GET", "/?AWSAccessKeyId=test&Signature=test&Expires=test", True, False),
            ("GET", "/?acl&AWSAccessKeyId=test&Signature=test&Expires=test", True, False),
            ("GET", "/?acl&AWSAccessKey=test", False, False),
            ("GET", "/?acl&AWSAccessKeyId=test", False, True),
        ]

        for method, request_path, is_sig_v2, will_raise in request_paths:
            fake_context = self._create_fake_context_from_path(request_path, method)
            query_args = set(fake_context.request.args)
            if not will_raise:
                assert presigned_url.is_valid_sig_v2(query_args) == is_sig_v2
            else:
                with pytest.raises(Exception):
                    presigned_url.is_valid_sig_v2(query_args)

    def test_is_valid_presigned_url_v4(self):
        # structure: method, path, is_sig_v4, will_raise
        request_paths = [
            (
                "GET",
                "/?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Credential=test&X-Amz-Date=test&X-Amz-Expires=test&X-Amz-SignedHeaders=host&X-Amz-Signature=test",
                True,
                False,
            ),
            ("GET", "/?acl", False, False),
            ("GET", "/?AWSAccessKeyId=test&Signature=test&Expires=test", False, False),
            (
                "GET",
                "/?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Credential=test&X-Amz-Date=test&X-Amz-Expires=test&X-Amz-SignedHeaders=host&X-Amz-Signature=test",
                True,
                False,
            ),
            (
                "PUT",
                "/?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Credential=test&X-Amz-Date=test&X-Amz-Expires=test&X-Amz-SignedHeaders=host&X-Amz-Signature=test",
                True,
                False,
            ),
            (
                "GET",
                "/?acl&X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Credential=test&X-Amz-Date=test&X-Amz-Expires=test&X-Amz-SignedHeaders=host&X-Amz-Signature=test",
                True,
                False,
            ),
            (
                "GET",
                "/?acl&X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Date=testX-Amz-Expires=test&X-Amz-SignedHeaders=host&X-Amz-Signature=test",
                True,
                True,
            ),
            (
                "GET",
                "/?acl&X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Credential=test&X-Amz-Date=testX-Amz-Expires=test&X-Amz-SignedHeaders=host",
                True,
                True,
            ),
            (
                "GET",
                "/?X-Amz-Credential=test&X-Amz-Date=testX-Amz-Expires=test&X-Amz-SignedHeaders=host&X-Amz-Signature=test",
                True,
                True,
            ),
        ]

        for method, request_path, is_sig_v4, will_raise in request_paths:
            fake_context = self._create_fake_context_from_path(request_path, method)
            query_args = set(fake_context.request.args)
            if not will_raise:
                assert presigned_url.is_valid_sig_v4(query_args) == is_sig_v4
            else:
                with pytest.raises(Exception):
                    presigned_url.is_valid_sig_v4(query_args)
