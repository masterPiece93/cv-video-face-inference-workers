"""Generated gRPC stubs for fdetect service."""
import grpc

from common.services.encoding.pbgen import safe_pb2 as _safe_pb2


class FaceDetectStub(object):
    """gRPC stub for the FaceDetect service."""

    def __init__(self, channel):
        self.Detect = channel.unary_unary(
            '/fdetect.pb.FaceDetect/Detect',
            request_serializer=_safe_pb2.FaceDetectRequest.SerializeToString,
            response_deserializer=_safe_pb2.FaceDetectResponse.FromString,
        )
        self.Health = channel.unary_unary(
            '/fdetect.pb.Health/get',
            request_serializer=_safe_pb2.HealthRequest.SerializeToString,
            response_deserializer=_safe_pb2.HealthResponse.FromString,
        )
